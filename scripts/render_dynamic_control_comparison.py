#!/usr/bin/env python3
"""Render an SVG comparison of Oculizer dynamic-control profiles for one WAV."""

from __future__ import annotations

import argparse
import contextlib
import html
import io
import json
import logging
import math
import random
import sys
from collections import Counter, deque
from pathlib import Path
from statistics import mode

import librosa
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from oculizer.automatic import AutomaticSceneRouter
from oculizer.rms_graph import SCENE_COLOR_FAMILIES, scene_visual
from oculizer.runtime_config import (
    configured_dynamic_controls,
    configured_fast_detection,
    configured_prediction,
    configured_silence,
    configured_speech,
    load_runtime_config,
)
from oculizer.scene_predictors import get_predictor, list_available_versions


logger = logging.getLogger("dynamic-control-comparison")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Compare off and every configured dynamic-control profile on one WAV"
    )
    parser.add_argument("wav", type=Path, help="Input PCM WAV file")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "docs" / "dynamic_control_comparison.svg",
        help="Output SVG path (default: docs/dynamic_control_comparison.svg)",
    )
    parser.add_argument(
        "--statistics-output", type=Path, default=None,
        help="Optional reproducible JSON scene statistics output",
    )
    parser.add_argument("--config", type=Path, default=None,
                        help="Oculizer configuration (default: config/oculizer.json)")
    parser.add_argument("--predictor-version", choices=list_available_versions(), default="v6")
    parser.add_argument("--raw-only", action="store_true",
                        help="Render only the neutral raw/off panel")
    parser.add_argument("--prediction-hop-seconds", type=float, default=1.0,
                        help="Seconds between expensive model inferences (default: 1.0)")
    parser.add_argument("--simulation-step-seconds", type=float, default=0.1,
                        help="Routing and graph sampling interval (default: 0.1)")
    parser.add_argument("--scene-max-duration", type=float, default=40.0)
    parser.add_argument("--off-cache-size", type=int, default=10,
                        help="Startup cache used by the neutral off panel (default: 10)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--width", type=int, default=1400)
    args = parser.parse_args(argv)
    if not args.wav.is_file() or args.wav.suffix.casefold() != ".wav":
        parser.error("wav must be an existing .wav file")
    if args.prediction_hop_seconds < args.simulation_step_seconds:
        parser.error("--prediction-hop-seconds must be >= --simulation-step-seconds")
    if not 0.05 <= args.simulation_step_seconds <= 2.0:
        parser.error("--simulation-step-seconds must be between 0.05 and 2")
    if not 0.1 <= args.prediction_hop_seconds <= 10.0:
        parser.error("--prediction-hop-seconds must be between 0.1 and 10")
    if not 600 <= args.width <= 4000:
        parser.error("--width must be between 600 and 4000")
    if not 1 <= args.off_cache_size <= 100:
        parser.error("--off-cache-size must be between 1 and 100")
    return args


def comparison_dynamic_controls(config, raw_only=False):
    """Resolve configured comparison profiles, or suppress them on request."""
    if raw_only:
        return {}
    return configured_dynamic_controls(config)


def xterm_rgb(index: int) -> str:
    """Convert one xterm-256 palette index to a CSS color."""
    basic = (
        (0, 0, 0), (128, 0, 0), (0, 128, 0), (128, 128, 0),
        (0, 0, 128), (128, 0, 128), (0, 128, 128), (192, 192, 192),
        (128, 128, 128), (255, 0, 0), (0, 255, 0), (255, 255, 0),
        (0, 0, 255), (255, 0, 255), (0, 255, 255), (255, 255, 255),
    )
    if index < 16:
        red, green, blue = basic[index]
    elif index < 232:
        cube = index - 16
        levels = (0, 95, 135, 175, 215, 255)
        red = levels[cube // 36]
        green = levels[(cube % 36) // 6]
        blue = levels[cube % 6]
    else:
        red = green = blue = 8 + (index - 232) * 10
    return f"#{red:02x}{green:02x}{blue:02x}"


def scene_style(scene: str) -> tuple[str, str]:
    visual = scene_visual(scene)
    palette_index = SCENE_COLOR_FAMILIES[visual.family][visual.shade]
    return visual.symbol, xterm_rgb(palette_index)


def load_scene_durations(config) -> tuple[set[str], dict[str, float]]:
    """Read the compact logical catalog used by the runtime."""
    metadata = config.get("lighting", {}).get("scene_metadata", {})
    names = set(metadata)
    durations = {
        name: float(data["max_duration_seconds"])
        for name, data in metadata.items()
        if isinstance(data.get("max_duration_seconds"), (int, float))
        and not isinstance(data.get("max_duration_seconds"), bool)
    }
    return names, durations


def analyse_wav(path: Path, predictor_version: str, window_seconds: float,
                prediction_hop: float, sample_rate: int = 48000,
                semantic_window_seconds: float | None = None,
                semantic_interval_seconds: float = 0.5):
    logger.info("Loading %s", path)
    audio, _ = librosa.load(path, sr=sample_rate, mono=True, dtype=np.float32)
    duration = len(audio) / sample_rate
    if duration < window_seconds:
        audio = np.pad(audio, (0, math.ceil(window_seconds * sample_rate) - len(audio)))
        duration = len(audio) / sample_rate

    step_samples = max(1, round(0.1 * sample_rate))
    rms = []
    for start in range(0, len(audio), step_samples):
        chunk = audio[start:start + step_samples]
        rms.append(float(np.sqrt(np.mean(np.square(chunk, dtype=np.float64)))))

    predictor_class = get_predictor(predictor_version)
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
        predictor = predictor_class(sr=sample_rate, seed=0)

    prediction_times = np.arange(window_seconds, duration + 1e-9, prediction_hop)
    window_samples = round(window_seconds * sample_rate)
    predictions = []
    total = len(prediction_times)
    for number, end_seconds in enumerate(prediction_times, 1):
        end = min(len(audio), round(end_seconds * sample_rate))
        window = audio[max(0, end - window_samples):end]
        if len(window) < window_samples:
            window = np.pad(window, (window_samples - len(window), 0))
        scene, cluster = predictor.predict(window, return_cluster=True)
        artistic_scores = dict(predictor.last_audioset_scores or {})
        predictions.append({
            "time": float(end_seconds),
            "scene": str(scene),
            "cluster": int(cluster),
            "scores": artistic_scores,
        })
        if number == 1 or number % 25 == 0 or number == total:
            logger.info("Inference %d/%d", number, total)
    semantic_predictions = []
    if semantic_window_seconds is not None:
        semantic_samples = max(1, round(semantic_window_seconds * sample_rate))
        semantic_times = np.arange(
            semantic_window_seconds, duration + 1e-9, semantic_interval_seconds
        )
        for end_seconds in semantic_times:
            end = min(len(audio), round(end_seconds * sample_rate))
            semantic_audio = audio[max(0, end - semantic_samples):end]
            if len(semantic_audio) < semantic_samples:
                semantic_audio = np.pad(
                    semantic_audio, (semantic_samples - len(semantic_audio), 0)
                )
            semantic_predictions.append({
                "time": float(end_seconds),
                "scores": predictor.get_semantic_scores(semantic_audio),
            })
    return np.asarray(rms, dtype=np.float64), duration, predictions, semantic_predictions


class SimulationEngine:
    def __init__(self, cache_size, scene_names, scene_durations):
        self.scene_cache_size = cache_size
        self.scene_cache = deque(maxlen=cache_size)
        self.current_predicted_scene = None
        self.current_audio_rms = None
        self.current_audioset_scores = None
        self.current_fast_audioset_scores = None
        self.active_scene = None
        self.scene_names = scene_names
        self.scene_durations = scene_durations
        self.prediction_suspended = False
        self.prediction_reset_generation = 0

    def set_scene_cache_size(self, size):
        self.scene_cache_size = size
        self.scene_cache = deque(self.scene_cache, maxlen=size)

    def set_prediction_suspended(self, suspended):
        self.prediction_suspended = bool(suspended)
        if suspended:
            self.scene_cache.clear()
            self.current_predicted_scene = None

    def resolve_scene_target(self, scene):
        return scene if scene in self.scene_names else None

    def change_scene(self, scene):
        target = self.resolve_scene_target(scene)
        if target is None:
            return False
        self.active_scene = target
        return True

    def get_scene_max_duration(self, scene):
        return self.scene_durations.get(scene)

    def accept_prediction(self, scene, scores, fast_scores=None):
        if self.prediction_suspended:
            return
        self.scene_cache.append(scene)
        self.current_predicted_scene = mode(self.scene_cache)
        self.current_audioset_scores = scores
        self.current_fast_audioset_scores = fast_scores

    def reset_prediction_state(self):
        self.scene_cache.clear()
        self.current_predicted_scene = None
        self.prediction_reset_generation += 1


def simulate_profile(name, profile, rms, duration, predictions, simulation_step,
                     silence_config, speech_config, scene_names, scene_durations,
                     scene_max_duration, seed, semantic_predictions=()):
    now = [0.0]
    engine = SimulationEngine(profile["cache"], scene_names, scene_durations)
    router = AutomaticSceneRouter(
        engine,
        silence_config=silence_config,
        speech_config=speech_config,
        clock=lambda: now[0],
        scene_rate_limit=profile["rate"],
        scene_throttle=profile["throttle"],
        scene_max_duration=scene_max_duration,
        random_source=random.Random(seed).random,
    )
    result = []
    prediction_index = 0
    held_prediction = None
    held_scores = None
    held_fast_scores = None
    semantic_index = 0
    steps = math.ceil(duration / simulation_step)
    for step in range(steps + 1):
        now[0] = min(duration, step * simulation_step)
        while prediction_index < len(predictions) and predictions[prediction_index]["time"] <= now[0] + 1e-9:
            held_prediction = predictions[prediction_index]["scene"]
            held_scores = predictions[prediction_index]["scores"]
            prediction_index += 1
        while (
            semantic_index < len(semantic_predictions)
            and semantic_predictions[semantic_index]["time"] <= now[0] + 1e-9
        ):
            held_fast_scores = semantic_predictions[semantic_index]["scores"]
            semantic_index += 1
        rms_index = min(len(rms) - 1, int(now[0] / 0.1))
        engine.current_audio_rms = float(rms[rms_index])
        if held_prediction is not None:
            # Runtime normally receives one prediction every 0.1 s. Repeating
            # the latest expensive offline inference preserves that cache-time
            # meaning while keeping documentation generation practical.
            engine.accept_prediction(held_prediction, held_scores, held_fast_scores)
        reset_generation = engine.prediction_reset_generation
        router.step()
        if engine.prediction_reset_generation != reset_generation:
            # Match the runtime: after speech ends, do not refill the cache
            # with the prediction produced before that transition.
            held_prediction = None
            held_scores = None
        result.append((now[0], engine.current_audio_rms, engine.active_scene))
    logger.info("Simulated %s: %d scene changes", name, count_transitions(result))
    return result


def count_transitions(samples):
    previous = None
    count = 0
    for _time, _rms, scene in samples:
        if scene is not None and previous is not None and scene != previous:
            count += 1
        if scene is not None:
            previous = scene
    return count


def summarize_simulation(samples, duration):
    """Summarize sampled scene state as transitions and wall-clock occupancy."""
    durations = Counter()
    transitions = []
    previous = None
    interval_start = None
    for index, (timestamp, _rms, scene) in enumerate(samples):
        end = samples[index + 1][0] if index + 1 < len(samples) else duration
        if end > timestamp:
            durations[scene or "<unrouted>"] += end - timestamp
        if scene != previous:
            if previous is not None and interval_start is not None:
                transitions[-1]["end_seconds"] = round(timestamp, 6)
                transitions[-1]["duration_seconds"] = round(timestamp - interval_start, 6)
            if scene is not None:
                interval_start = timestamp
                transitions.append({
                    "scene": scene,
                    "start_seconds": round(timestamp, 6),
                })
            else:
                interval_start = None
        previous = scene
    if transitions and "end_seconds" not in transitions[-1]:
        transitions[-1]["end_seconds"] = round(duration, 6)
        transitions[-1]["duration_seconds"] = round(duration - interval_start, 6)
    scene_statistics = [
        {
            "scene": scene,
            "duration_seconds": round(seconds, 6),
            "percentage_of_file": round(seconds / duration * 100.0, 4),
        }
        for scene, seconds in sorted(durations.items(), key=lambda item: (-item[1], item[0]))
    ]
    return {
        "scene_change_count": count_transitions(samples),
        "scene_statistics": scene_statistics,
        "transitions": transitions,
    }


def write_statistics(output, source, predictor_version, window_seconds,
                     prediction_hop, simulation_step, seed, duration,
                     predictions, profiles, simulations, semantic_predictions=(),
                     semantic_window_seconds=None, semantic_interval_seconds=None):
    """Persist enough inputs and results to reproduce a before/after comparison."""
    payload = {
        "schema_version": 1,
        "source": str(source),
        "predictor_version": predictor_version,
        "prediction_window_seconds": window_seconds,
        "prediction_hop_seconds": prediction_hop,
        "simulation_step_seconds": simulation_step,
        "duration_seconds": round(duration, 6),
        "random_seed": seed,
        "fast_semantic_window_seconds": semantic_window_seconds,
        "fast_semantic_interval_seconds": semantic_interval_seconds,
        "raw_predictions": predictions,
        "fast_semantic_predictions": list(semantic_predictions),
        "profiles": {
            name: {
                "policy": {
                    "cache": profile["cache"],
                    "rate": profile["rate"],
                    "throttle": profile["throttle"],
                },
                **summarize_simulation(simulations[name], duration),
            }
            for name, profile in profiles
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def svg_path(samples, x0, y0, width, height, duration, rms_max):
    points = []
    for timestamp, rms, _scene in samples:
        x = x0 + timestamp / duration * width
        y = y0 + height - min(1.0, rms / rms_max) * height
        points.append(f"{x:.1f},{y:.1f}")
    return " ".join(points)


def render_svg(output: Path, source: Path, predictor_version: str, prediction_hop: float,
               profiles, simulations, duration: float, width: int):
    margin_x = 76
    top = 92
    panel_height = 155
    panel_gap = 54
    plot_width = width - margin_x - 36
    plot_height = panel_height - 28
    scenes = sorted({scene for samples in simulations.values()
                     for _timestamp, _rms, scene in samples if scene is not None})
    legend_columns = 5
    legend_rows = max(1, math.ceil(len(scenes) / legend_columns))
    legend_height = 48 + legend_rows * 26
    height = top + len(profiles) * (panel_height + panel_gap) + legend_height
    rms_max = max(1e-6, max(rms for samples in simulations.values() for _time, rms, _scene in samples) * 1.05)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#10141b"/>',
        '<style>text{font-family:Inter,DejaVu Sans,sans-serif}.title{font-size:25px;font-weight:700;fill:#f4f7fb}'
        '.sub{font-size:14px;fill:#aeb9c8}.panel{font-size:18px;font-weight:700;fill:#e7edf6}'
        '.axis{font-size:12px;fill:#8996a8}.legend{font-size:13px;fill:#cbd4e1}</style>',
        f'<text class="title" x="{margin_x}" y="38">Oculizer dynamic-control comparison</text>',
        f'<text class="sub" x="{margin_x}" y="64">{html.escape(source.name)} · predictor {html.escape(predictor_version)} · inference hop {prediction_hop:g}s · identical RMS and raw predictions in every panel</text>',
    ]
    for panel_number, (name, profile) in enumerate(profiles):
        samples = simulations[name]
        panel_y = top + panel_number * (panel_height + panel_gap)
        rate = "Off" if profile["rate"] is None else f"{profile['rate'][0]}/{profile['rate'][1]:g}s"
        throttle = ("Off" if profile["throttle"] is None
                    else f"{profile['throttle'][0]}/{profile['throttle'][1]:g}s")
        transitions = count_transitions(samples)
        parts.extend([
            f'<text class="panel" x="{margin_x}" y="{panel_y - 12}">{html.escape(name)}</text>',
            f'<text class="sub" x="{margin_x + 145}" y="{panel_y - 12}">cache {profile["cache"]} · rate {rate} · throttle {throttle} · {transitions} changes</text>',
            f'<rect x="{margin_x}" y="{panel_y}" width="{plot_width}" height="{plot_height}" rx="5" fill="#151b24" stroke="#303b4b"/>',
        ])
        for fraction in (0.25, 0.5, 0.75):
            grid_y = panel_y + plot_height * (1 - fraction)
            parts.append(f'<line x1="{margin_x}" y1="{grid_y:.1f}" x2="{margin_x + plot_width}" y2="{grid_y:.1f}" stroke="#25303e" stroke-width="1"/>')
        path = svg_path(samples, margin_x, panel_y, plot_width, plot_height, duration, rms_max)
        parts.append(f'<polyline points="{path}" fill="none" stroke="#8fd3ff" stroke-width="1.7" stroke-linejoin="round"/>')
        parts.append(f'<text class="axis" x="{margin_x - 8}" y="{panel_y + 10}" text-anchor="end">{rms_max:.3f}</text>')
        parts.append(f'<text class="axis" x="{margin_x - 8}" y="{panel_y + plot_height}" text-anchor="end">0</text>')

        previous = None
        for timestamp, rms, scene in samples:
            if scene is None or scene == previous:
                continue
            symbol, color = scene_style(scene)
            x = margin_x + timestamp / duration * plot_width
            y = panel_y + plot_height - min(1.0, rms / rms_max) * plot_height
            parts.append(
                f'<text x="{x:.1f}" y="{y + 5:.1f}" text-anchor="middle" '
                f'font-family="DejaVu Sans" font-size="17" font-weight="700" fill="{color}" '
                f'stroke="#10141b" stroke-width="2.5" paint-order="stroke">{html.escape(symbol)}</text>'
            )
            previous = scene

    legend_y = top + len(profiles) * (panel_height + panel_gap) + 4
    parts.append(f'<text class="panel" x="{margin_x}" y="{legend_y}">Scene markers</text>')
    column_width = plot_width / legend_columns
    for index, scene in enumerate(scenes):
        row, column = divmod(index, legend_columns)
        x = margin_x + column * column_width
        y = legend_y + 30 + row * 26
        symbol, color = scene_style(scene)
        parts.append(f'<text x="{x}" y="{y}" font-family="DejaVu Sans" font-size="17" font-weight="700" fill="{color}">{html.escape(symbol)}</text>')
        parts.append(f'<text class="legend" x="{x + 23}" y="{y}">{html.escape(scene)}</text>')
    parts.append("</svg>")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(parts) + "\n", encoding="utf-8")


def main(argv=None):
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("oculizer.automatic").setLevel(logging.WARNING)
    config = load_runtime_config(args.config)
    dynamic_controls = comparison_dynamic_controls(config, raw_only=args.raw_only)
    prediction_config = configured_prediction(config)
    fast_detection_config = configured_fast_detection(config)
    silence_config = configured_silence(config)
    speech_config = configured_speech(config)
    scene_names, scene_durations = load_scene_durations(config)
    rms, duration, predictions, semantic_predictions = analyse_wav(
        args.wav, args.predictor_version, prediction_config.window_seconds,
        args.prediction_hop_seconds,
        semantic_window_seconds=(
            fast_detection_config.speech.window_seconds
            if fast_detection_config.enabled and fast_detection_config.speech.enabled
            else None
        ),
        semantic_interval_seconds=fast_detection_config.speech.interval_seconds,
    )
    profiles = [("raw (off)", {"cache": args.off_cache_size,
                                "rate": None, "throttle": None}),
                *dynamic_controls.items()]
    simulations = {}
    for name, profile in profiles:
        simulations[name] = simulate_profile(
            name, profile, rms, duration, predictions, args.simulation_step_seconds,
            silence_config, speech_config, scene_names, scene_durations,
            args.scene_max_duration, args.seed,
            semantic_predictions=semantic_predictions,
        )
    render_svg(
        args.output, args.wav, args.predictor_version, args.prediction_hop_seconds,
        profiles, simulations, duration, args.width,
    )
    print(f"Wrote {args.output}")
    if args.statistics_output is not None:
        write_statistics(
            args.statistics_output, args.wav, args.predictor_version,
            prediction_config.window_seconds, args.prediction_hop_seconds,
            args.simulation_step_seconds, args.seed, duration, predictions,
            profiles, simulations,
            semantic_predictions=semantic_predictions,
            semantic_window_seconds=fast_detection_config.speech.window_seconds,
            semantic_interval_seconds=fast_detection_config.speech.interval_seconds,
        )
        print(f"Wrote {args.statistics_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
