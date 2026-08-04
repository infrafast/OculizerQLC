#!/usr/bin/env python3
"""Train and review a concert-specific v6 scene clusterer.

The script deliberately separates statistical clustering from artistic scene
assignment. It writes a provisional ``party`` mapping plus representative WAV
excerpts and a Markdown report that an operator can use to edit the mapping.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import io
import json
import logging
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import joblib
import librosa
import numpy as np
import soundfile as sf
import torch
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from oculizer.scene_predictors.v4.predictor import (  # noqa: E402
    ScenePredictor as V4ScenePredictor,
    set_deterministic_seeds,
)

AUDIO_EXTENSIONS = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".wav"}
FEATURE_SCHEMA = "efficientat-dymn20_as-1920+v4-mfcc-mean-128"


@dataclass(frozen=True)
class WindowRecord:
    source: str
    start_seconds: float
    rms: float
    speech: float
    singing: float
    music: float


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Train a v6 predictor from representative concert recordings"
    )
    parser.add_argument("--input", required=True, type=Path, help="Audio file or directory")
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "oculizer/scene_predictors/v6",
        help="v6 model directory",
    )
    parser.add_argument("--clusters", type=int, default=30, help="KMeans cluster count")
    parser.add_argument("--window-seconds", type=float, default=4.0)
    parser.add_argument("--hop-seconds", type=float, default=2.0)
    parser.add_argument("--sample-rate", type=int, default=48000)
    parser.add_argument(
        "--max-windows-per-track",
        type=int,
        default=200,
        help="Evenly subsample long tracks; 0 keeps every window",
    )
    parser.add_argument(
        "--silence-rms",
        type=float,
        default=0.005,
        help="Discard windows below this RMS; use 0 to retain silence",
    )
    parser.add_argument("--pca-components", type=int, default=128)
    parser.add_argument("--representatives", type=int, default=3)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--features-cache",
        type=Path,
        help="NPZ feature cache (default: OUTPUT/training_features.npz)",
    )
    parser.add_argument(
        "--reuse-features",
        action="store_true",
        help="Reuse an existing compatible feature cache without decoding audio",
    )
    parser.add_argument(
        "--mapping",
        type=Path,
        help="Optional JSON mapping with exactly one scene for every cluster",
    )
    parser.add_argument("--extract-only", action="store_true")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace existing v6 model artefacts",
    )
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if args.clusters < 2:
        raise ValueError("--clusters must be at least 2")
    if args.window_seconds <= 0 or args.hop_seconds <= 0:
        raise ValueError("window and hop durations must be positive")
    if args.sample_rate != 48000:
        raise ValueError("v5/v6 EfficientAT features require --sample-rate 48000")
    if args.max_windows_per_track < 0 or args.pca_components < 1:
        raise ValueError("window limit and PCA components cannot be negative")
    if args.representatives < 1:
        raise ValueError("--representatives must be at least 1")


def discover_audio(path: Path) -> list[Path]:
    if path.is_file():
        files = [path] if path.suffix.lower() in AUDIO_EXTENSIONS else []
    elif path.is_dir():
        files = [candidate for candidate in path.rglob("*") if candidate.suffix.lower() in AUDIO_EXTENSIONS]
    else:
        raise FileNotFoundError(path)
    files = sorted(candidate.resolve() for candidate in files)
    if not files:
        raise ValueError(f"No supported audio files found under {path}")
    return files


def window_starts(sample_count: int, window_size: int, hop_size: int, maximum: int) -> np.ndarray:
    if sample_count <= window_size:
        starts = np.array([0], dtype=np.int64)
    else:
        starts = np.arange(0, sample_count - window_size + 1, hop_size, dtype=np.int64)
        final_start = sample_count - window_size
        if starts[-1] != final_start:
            starts = np.append(starts, final_start)
    if maximum and len(starts) > maximum:
        indexes = np.linspace(0, len(starts) - 1, maximum, dtype=np.int64)
        starts = starts[indexes]
    return np.unique(starts)


def load_window(source: Path, start_seconds: float, duration: float, sample_rate: int) -> np.ndarray:
    audio, _ = librosa.load(
        source,
        sr=sample_rate,
        mono=True,
        offset=max(0.0, start_seconds),
        duration=duration,
        dtype=np.float32,
    )
    required = int(round(duration * sample_rate))
    if len(audio) < required:
        audio = np.pad(audio, (0, required - len(audio)))
    return np.asarray(audio[:required], dtype=np.float32)


class V4FeatureExtractor:
    """Feature-only adapter using the runtime's exact v4 extraction methods."""

    def __init__(self, sample_rate: int, seed: int):
        set_deterministic_seeds(seed)
        extractor = V4ScenePredictor.__new__(V4ScenePredictor)
        extractor.sr = sample_rate
        extractor.n_mfcc = 128
        extractor.n_fft = 2048
        extractor.hop_length = 512
        extractor.last_audioset_scores = None
        extractor.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        with contextlib.redirect_stdout(io.StringIO()):
            extractor._load_efficientat_model()
        self.extractor = extractor

    def extract(self, audio: np.ndarray) -> tuple[np.ndarray, dict[str, float]]:
        embedding = self.extractor.get_efficientat_embedding(audio)
        mfcc_features = self.extractor.extract_mfcc_features(audio)
        features = np.concatenate((embedding, mfcc_features)).astype(np.float32)
        return features, dict(self.extractor.last_audioset_scores)


def extract_features(args: argparse.Namespace, files: list[Path]) -> tuple[np.ndarray, list[WindowRecord]]:
    extractor = V4FeatureExtractor(args.sample_rate, args.seed)
    feature_rows: list[np.ndarray] = []
    records: list[WindowRecord] = []
    window_size = int(round(args.window_seconds * args.sample_rate))
    hop_size = int(round(args.hop_seconds * args.sample_rate))

    for file_number, source in enumerate(files, 1):
        duration = float(librosa.get_duration(path=source))
        starts = window_starts(
            int(round(duration * args.sample_rate)),
            window_size,
            hop_size,
            args.max_windows_per_track,
        )
        kept = 0
        print(f"[{file_number}/{len(files)}] {source.name}: {len(starts)} candidate windows")
        for start in starts:
            start_seconds = float(start) / args.sample_rate
            audio = load_window(source, start_seconds, args.window_seconds, args.sample_rate)
            rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))
            if rms < args.silence_rms:
                continue
            features, scores = extractor.extract(audio)
            feature_rows.append(features)
            records.append(
                WindowRecord(
                    source=str(source),
                    start_seconds=start_seconds,
                    rms=rms,
                    speech=scores["speech"],
                    singing=scores["singing"],
                    music=scores["music"],
                )
            )
            kept += 1
        print(f"  retained {kept} windows")
    if not feature_rows:
        raise ValueError("No non-silent windows were extracted")
    return np.stack(feature_rows), records


def save_cache(path: Path, features: np.ndarray, records: list[WindowRecord], args: argparse.Namespace) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        "schema": FEATURE_SCHEMA,
        "sample_rate": args.sample_rate,
        "window_seconds": args.window_seconds,
        "hop_seconds": args.hop_seconds,
    }
    np.savez_compressed(
        path,
        features=np.asarray(features, dtype=np.float32),
        sources=np.asarray([record.source for record in records]),
        starts=np.asarray([record.start_seconds for record in records]),
        rms=np.asarray([record.rms for record in records]),
        speech=np.asarray([record.speech for record in records]),
        singing=np.asarray([record.singing for record in records]),
        music=np.asarray([record.music for record in records]),
        metadata=json.dumps(metadata),
    )


def load_cache(path: Path, args: argparse.Namespace) -> tuple[np.ndarray, list[WindowRecord]]:
    with np.load(path, allow_pickle=False) as cached:
        metadata = json.loads(str(cached["metadata"]))
        expected = {
            "schema": FEATURE_SCHEMA,
            "sample_rate": args.sample_rate,
            "window_seconds": args.window_seconds,
            "hop_seconds": args.hop_seconds,
        }
        if metadata != expected:
            raise ValueError(f"Incompatible feature cache metadata: {metadata}; expected {expected}")
        features = np.asarray(cached["features"], dtype=np.float32)
        records = [
            WindowRecord(str(source), float(start), float(rms), float(speech), float(singing), float(music))
            for source, start, rms, speech, singing, music in zip(
                cached["sources"], cached["starts"], cached["rms"],
                cached["speech"], cached["singing"], cached["music"], strict=True,
            )
        ]
    return features, records


def fit_models(features: np.ndarray, args: argparse.Namespace):
    sample_count, feature_count = features.shape
    if args.clusters > sample_count:
        raise ValueError(f"--clusters {args.clusters} exceeds retained windows {sample_count}")
    component_count = min(args.pca_components, sample_count - 1, feature_count)
    if component_count < 1:
        raise ValueError("At least two retained windows are required")
    print(f"Fitting scaler on {sample_count} x {feature_count} features")
    scaler = StandardScaler()
    # The runtime concatenates EfficientAT float32 embeddings with librosa
    # float64 MFCC means, yielding float64 input. Fit every sklearn artefact on
    # that same dtype; mixed KMeans/PCA dtypes otherwise fail at prediction.
    scaled = scaler.fit_transform(np.asarray(features, dtype=np.float64))
    print(f"Fitting randomized PCA with {component_count} components")
    pca = PCA(n_components=component_count, svd_solver="randomized", random_state=args.seed)
    reduced = pca.fit_transform(scaled)
    print(f"Fitting KMeans with {args.clusters} clusters")
    kmeans = KMeans(n_clusters=args.clusters, n_init=10, random_state=args.seed)
    labels = kmeans.fit_predict(reduced)
    return scaler, pca, kmeans, reduced, labels


def load_mapping(path: Path | None, clusters: int) -> dict[str, str]:
    if path is None:
        return {str(cluster): "party" for cluster in range(clusters)}
    mapping = json.loads(path.read_text())
    expected = {str(cluster) for cluster in range(clusters)}
    if set(mapping) != expected or not all(isinstance(value, str) and value for value in mapping.values()):
        raise ValueError(f"Mapping must contain exactly the cluster keys 0..{clusters - 1}")
    return mapping


def nearest_representatives(reduced, labels, centers, count):
    result: dict[int, list[int]] = {}
    for cluster in range(len(centers)):
        indexes = np.flatnonzero(labels == cluster)
        distances = np.linalg.norm(reduced[indexes] - centers[cluster], axis=1)
        result[cluster] = indexes[np.argsort(distances)[:count]].tolist()
    return result


def write_review(
    output: Path,
    args: argparse.Namespace,
    records: list[WindowRecord],
    labels: np.ndarray,
    representatives: dict[int, list[int]],
) -> None:
    review_dir = output / "review"
    excerpts_dir = review_dir / "excerpts"
    excerpts_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    report = [
        "# v6 cluster review",
        "",
        "Listen to the representative excerpts, then replace each provisional `party` value in `scene_mapping.json`.",
        "Speech and silence remain independent runtime routes and should not be inferred solely from these clusters.",
        "",
        "| Cluster | Windows | RMS mean | Speech mean | Music mean | Representatives |",
        "|---:|---:|---:|---:|---:|---|",
    ]
    for cluster in range(args.clusters):
        indexes = np.flatnonzero(labels == cluster)
        names = []
        for rank, index in enumerate(representatives[cluster], 1):
            record = records[index]
            name = f"cluster_{cluster:03d}_{rank}_{Path(record.source).stem[:40]}.wav"
            audio = load_window(Path(record.source), record.start_seconds, args.window_seconds, args.sample_rate)
            sf.write(excerpts_dir / name, audio, args.sample_rate, subtype="PCM_16")
            names.append(f"[{name}](excerpts/{name})")
        rms_mean = float(np.mean([records[index].rms for index in indexes]))
        speech_mean = float(np.mean([records[index].speech for index in indexes]))
        music_mean = float(np.mean([records[index].music for index in indexes]))
        report.append(
            f"| {cluster} | {len(indexes)} | {rms_mean:.4f} | {speech_mean:.3f} | "
            f"{music_mean:.3f} | {', '.join(names)} |"
        )
        rows.append((cluster, len(indexes), rms_mean, speech_mean, music_mean))
    (review_dir / "cluster_report.md").write_text("\n".join(report) + "\n")
    with (review_dir / "cluster_summary.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("cluster", "windows", "rms_mean", "speech_mean", "music_mean"))
        writer.writerows(rows)


def ensure_output_is_safe(output: Path, force: bool) -> None:
    artefacts = ("scaler.pkl", "pca.pkl", "kmeans.pkl", "scene_mapping.json")
    existing = [output / name for name in artefacts if (output / name).exists()]
    if existing and not force:
        names = ", ".join(path.name for path in existing)
        raise FileExistsError(f"Output already contains {names}; pass --force to replace the model")


def main() -> int:
    args = build_parser().parse_args()
    validate_args(args)
    args.output = args.output.resolve()
    cache_path = (args.features_cache or args.output / "training_features.npz").resolve()
    files = discover_audio(args.input)
    print(f"Discovered {len(files)} audio files")

    if args.reuse_features:
        features, records = load_cache(cache_path, args)
        print(f"Reused {len(records)} cached windows from {cache_path}")
    else:
        features, records = extract_features(args, files)
        save_cache(cache_path, features, records, args)
        print(f"Saved {len(records)} windows to {cache_path}")
    if args.extract_only:
        return 0

    ensure_output_is_safe(args.output, args.force)
    scaler, pca, kmeans, reduced, labels = fit_models(features, args)
    mapping = load_mapping(args.mapping, args.clusters)
    args.output.mkdir(parents=True, exist_ok=True)
    joblib.dump(scaler, args.output / "scaler.pkl")
    joblib.dump(pca, args.output / "pca.pkl")
    joblib.dump(kmeans, args.output / "kmeans.pkl")
    (args.output / "scene_mapping.json").write_text(json.dumps(mapping, indent=2) + "\n")
    metadata = {
        "version": "v6",
        "feature_schema": FEATURE_SCHEMA,
        "sample_rate": args.sample_rate,
        "window_seconds": args.window_seconds,
        "hop_seconds": args.hop_seconds,
        "clusters": args.clusters,
        "pca_components": int(pca.n_components_),
        "training_windows": len(records),
        "source_files": len({record.source for record in records}),
        "seed": args.seed,
        "explained_variance": float(np.sum(pca.explained_variance_ratio_)),
        "mapping_status": "approved" if args.mapping else "provisional",
    }
    (args.output / "model_metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    ready_marker = args.output / ".ready"
    if args.mapping:
        ready_marker.write_text("Artistically reviewed scene mapping\n")
    elif ready_marker.exists():
        ready_marker.unlink()
    representatives = nearest_representatives(reduced, labels, kmeans.cluster_centers_, args.representatives)
    write_review(args.output, args, records, labels, representatives)
    print(f"v6 model written to {args.output}")
    print(f"Review {args.output / 'review/cluster_report.md'} before using it in a show")
    if not args.mapping:
        print("v6 remains unavailable at runtime until retrained with --mapping REVIEWED_MAPPING")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    raise SystemExit(main())
