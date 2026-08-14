"""Bounded, revision-aware editing of operator-facing configuration fields."""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Any, Callable

from oculizer.runtime_config import DEFAULT_CONFIG_PATH, load_runtime_config


@dataclass(frozen=True)
class ConfigField:
    path: str
    label: str
    kind: str
    help: str
    apply_mode: str = "hot"
    unit: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    recommended_minimum: float | None = None
    recommended_maximum: float | None = None
    choices: tuple[str, ...] = ()

    def public(self) -> dict[str, Any]:
        result = asdict(self)
        result["choices"] = list(self.choices)
        result["section"] = _section_for_path(self.path)
        return result


def _field(path, label, kind, help_text, **kwargs):
    return ConfigField(path, label, kind, help_text, **kwargs)


def _section_for_path(path: str) -> str:
    if path.startswith("web."):
        return "Web interface"
    if path == "audio.input_device":
        return "Audio input"
    if path.startswith("audio.prediction."):
        return "Scene prediction"
    if path.startswith("audio.fast_detection."):
        return "Fast speech detection"
    if path.startswith("audio.silence."):
        return "Silence detection"
    if path.startswith("audio.speech."):
        return "Speech and announcement"
    if path.startswith("audio.master_modulation."):
        return "Master control"
    if ".bands.bass." in path:
        return "Bass control"
    if ".bands.mid." in path:
        return "Mid control"
    if ".bands.high." in path:
        return "High control"
    if path.startswith("audio.frequency_modulation."):
        return "Frequency controls"
    if path.startswith("control."):
        return "Scene transitions"
    return "Other"


CONFIG_FIELDS = (
    _field("web.enabled", "Web interface", "boolean",
           "Start the embedded Web child with the headless runtime.", apply_mode="restart"),
    _field("web.bind", "Web bind address", "text",
           "Host name or network address used by the embedded Web listener.", apply_mode="restart"),
    _field("web.port", "Web port", "number",
           "TCP port used by the embedded Web listener.", apply_mode="restart",
           minimum=1, maximum=65535, recommended_minimum=1024, recommended_maximum=49151),
    _field("web.graph_enabled", "Web graph", "boolean",
           "Enable the low-rate browser RMS and scene timeline."),
    _field("control.scene_max_duration_seconds", "Maximum scene duration", "number",
           "Default maximum duration before an ordinary automatic scene is replaced; per-scene metadata may override it.",
           unit="s", minimum=0.5, maximum=3600, recommended_minimum=15, recommended_maximum=120),
    _field("audio.input_device", "Audio input", "text",
           "OS default, device name, partial name, or input-device index.",
           apply_mode="restart"),
    _field("audio.prediction.window_seconds", "Prediction window", "number",
           "Audio duration evaluated by the artistic scene predictor.",
           apply_mode="restart", unit="s", minimum=0.5, maximum=10,
           recommended_minimum=2, recommended_maximum=4),
    _field("audio.prediction.interval_seconds", "Prediction interval", "number",
           "Delay between artistic predictions. Values below 0.75 s are not recommended on Raspberry Pi 5.",
           apply_mode="restart", unit="s", minimum=0.1, maximum=10,
           recommended_minimum=0.75, recommended_maximum=2),
    _field("audio.fast_detection.enabled", "Fast detection", "boolean",
           "Enable the low-cadence priority speech detector.", apply_mode="restart"),
    _field("audio.fast_detection.speech.enabled", "Fast speech detection", "boolean",
           "Enable speech analysis in the priority detector.", apply_mode="restart"),
    _field("audio.fast_detection.speech.window_seconds", "Fast speech window", "number",
           "Audio duration used for each priority speech decision.",
           apply_mode="restart", unit="s", minimum=0.25, maximum=4,
           recommended_minimum=1, recommended_maximum=2),
    _field("audio.fast_detection.speech.interval_seconds", "Fast speech interval", "number",
           "Delay between priority speech analyses. Values below 0.75 s are not recommended on Raspberry Pi 5.",
           apply_mode="restart", unit="s", minimum=0.5, maximum=5,
           recommended_minimum=0.75, recommended_maximum=2),
    _field("audio.silence.enabled", "Silence detection", "boolean",
           "Route sustained low-level audio to the configured silent scene."),
    _field("audio.silence.threshold", "Silence threshold", "number",
           "RMS level at or below which silence timing starts.",
           minimum=0, maximum=1, recommended_minimum=0.0001, recommended_maximum=0.02),
    _field("audio.silence.resume_threshold", "Silence resume threshold", "number",
           "RMS level required to leave silence; it must exceed the silence threshold.",
           minimum=0, maximum=1, recommended_minimum=0.0002, recommended_maximum=0.05),
    _field("audio.silence.duration_seconds", "Silence duration", "number",
           "Continuous time below the threshold before the silent scene is selected.",
           unit="s", minimum=0, maximum=60, recommended_minimum=0.5, recommended_maximum=5),
    _field("audio.silence.scene", "Silent scene", "text",
           "Logical QLC+ button caption used for detected silence."),
    _field("audio.speech.enabled", "Speech routing", "boolean",
           "Route sustained dominant speech to the announcement scene."),
    _field("audio.speech.threshold", "Speech threshold", "number",
           "Minimum semantic speech score required to consider speech dominant.",
           minimum=0, maximum=1, recommended_minimum=0.4, recommended_maximum=0.8),
    _field("audio.speech.music_margin", "Speech/music margin", "number",
           "Required score advantage over music before entering speech mode.",
           minimum=0, maximum=1, recommended_minimum=0.05, recommended_maximum=0.35),
    _field("audio.speech.minimum_duration_seconds", "Speech confirmation", "number",
           "Continuous dominant-speech time required before announcement routing.",
           unit="s", minimum=0, maximum=30, recommended_minimum=0.5, recommended_maximum=2),
    _field("audio.speech.release_duration_seconds", "Speech release", "number",
           "Continuous non-speech time required before returning to music.",
           unit="s", minimum=0, maximum=30, recommended_minimum=0.5, recommended_maximum=3),
    _field("audio.speech.scene", "Announcement scene", "text",
           "Logical QLC+ button caption used for dominant speech."),
)


def _add_modulation_fields() -> tuple[ConfigField, ...]:
    result = []
    for section, label in (("master_modulation", "Master"), ("frequency_modulation", "Frequency")):
        base = f"audio.{section}"
        result.extend((
            _field(f"{base}.enabled", f"{label} modulation", "boolean",
                   f"Enable continuous {label.lower()} level output."),
            _field(f"{base}.rate_hz", f"{label} update rate", "number",
                   "Maximum lighting-control update rate.", unit="Hz", minimum=1, maximum=60,
                   recommended_minimum=10, recommended_maximum=30),
            _field(f"{base}.smoothing_factor", f"{label} smoothing", "number",
                   "Blend factor: lower values move more slowly and higher values react faster.",
                   minimum=0, maximum=1, recommended_minimum=0.1, recommended_maximum=0.5),
            _field(f"{base}.change_threshold", f"{label} change threshold", "number",
                   "Minimum normalized change sent before the periodic refresh.",
                   minimum=0, maximum=1, recommended_minimum=0.005, recommended_maximum=0.1),
            _field(f"{base}.silence_value", f"{label} silence value", "number",
                   "Normalized value used when audio is below the configured floor.", minimum=0, maximum=1),
            _field(f"{base}.shutdown_value", f"{label} shutdown value", "number",
                   "Normalized safe value sent during a normal shutdown.", minimum=0, maximum=1),
            _field(f"{base}.refresh_seconds", f"{label} refresh interval", "number",
                   "Maximum delay before an unchanged control value is refreshed.",
                   unit="s", minimum=0.01, maximum=60, recommended_minimum=0.25, recommended_maximum=2),
        ))
    result.extend((
        _field("audio.master_modulation.parameter", "Master control", "text",
               "Logical QLC+ slider caption receiving the master value."),
        _field("audio.master_modulation.input_floor", "Master input floor", "number",
               "RMS level mapped to the configured silence value.", minimum=0, maximum=1),
        _field("audio.master_modulation.input_ceiling", "Master input ceiling", "number",
               "RMS level mapped to full output; it must exceed the input floor.", minimum=0, maximum=1),
    ))
    for band in ("bass", "mid", "high"):
        base = f"audio.frequency_modulation.bands.{band}"
        title = band.title()
        result.extend((
            _field(f"{base}.enabled", f"{title} modulation", "boolean",
                   f"Enable the {band} frequency-band control."),
            _field(f"{base}.parameter", f"{title} control", "text",
                   f"Logical QLC+ slider caption receiving the {band} value."),
            _field(f"{base}.low_hz", f"{title} low frequency", "number",
                   "Inclusive lower frequency bound.", unit="Hz", minimum=0, maximum=24000),
            _field(f"{base}.high_hz", f"{title} high frequency", "number",
                   "Exclusive upper bound; it must exceed the lower bound and remain usable at the analysis rate.",
                   unit="Hz", minimum=1, maximum=24000),
            _field(f"{base}.input_floor", f"{title} input floor", "number",
                   "Band energy mapped to the configured silence value.", minimum=0, maximum=10),
            _field(f"{base}.input_ceiling", f"{title} input ceiling", "number",
                   "Band energy mapped to full output; it must exceed the input floor.", minimum=0, maximum=10),
            _field(f"{base}.response", f"{title} response", "choice",
                   "Level follows sustained energy; transient emphasizes attacks.", choices=("level", "transient")),
            _field(f"{base}.baseline_smoothing", f"{title} baseline smoothing", "number",
                   "Adaptation rate used only by transient response.", minimum=0.0001, maximum=1,
                   recommended_minimum=0.005, recommended_maximum=0.1),
        ))
    return tuple(result)


CONFIG_FIELDS = CONFIG_FIELDS + _add_modulation_fields()
CONFIG_FIELD_BY_PATH = {field.path: field for field in CONFIG_FIELDS}


def _get_path(document: dict[str, Any], path: str) -> Any:
    value: Any = document
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _set_path(document: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    target = document
    for part in parts[:-1]:
        child = target.get(part)
        if not isinstance(child, dict):
            child = {}
            target[part] = child
        target = child
    target[parts[-1]] = value


def _revision(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class ConfigurationConflictError(RuntimeError):
    """Raised when an editor attempts to replace a newer file revision."""


class ConfigurationStore:
    """Edit one trusted configuration path with bounded atomic operations."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path).expanduser().resolve() if path else DEFAULT_CONFIG_PATH.resolve()
        self.backup_path = self.path.with_suffix(self.path.suffix + ".previous")
        self.lock = threading.RLock()

    def schema(self) -> list[dict[str, Any]]:
        return [field.public() for field in CONFIG_FIELDS]

    def read(self) -> dict[str, Any]:
        with self.lock:
            raw = self.path.read_bytes()
            config = load_runtime_config(self.path)
            return {
                "path": str(self.path),
                "revision": _revision(raw),
                "values": {field.path: _get_path(config, field.path) for field in CONFIG_FIELDS},
            }

    @staticmethod
    def _validate_field_value(field: ConfigField, value: Any) -> None:
        if field.kind == "boolean":
            if not isinstance(value, bool):
                raise ValueError(f"{field.path} must be a boolean")
        elif field.kind == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{field.path} must be numeric")
            if field.minimum is not None and value < field.minimum:
                raise ValueError(f"{field.path} must be at least {field.minimum}")
            if field.maximum is not None and value > field.maximum:
                raise ValueError(f"{field.path} must be at most {field.maximum}")
        elif field.kind == "text":
            valid = isinstance(value, str) or (
                field.path == "audio.input_device"
                and isinstance(value, int)
                and not isinstance(value, bool)
            )
            if not valid:
                raise ValueError(f"{field.path} must be text")
            if isinstance(value, str) and not value.strip():
                raise ValueError(f"{field.path} must not be empty")
        elif field.kind == "choice" and value not in field.choices:
            raise ValueError(f"{field.path} must be one of: {', '.join(field.choices)}")

    def _write_temp(self, config: dict[str, Any]) -> Path:
        descriptor, name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
        temp_path = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(config, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            load_runtime_config(temp_path)
            return temp_path
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def apply(self, changes: dict[str, Any], expected_revision: str,
              live_apply: Callable[[dict[str, Any], set[str]], None] | None = None) -> dict[str, Any]:
        if not isinstance(changes, dict) or not changes:
            raise ValueError("changes must be a non-empty object")
        with self.lock:
            old_raw = self.path.read_bytes()
            current_revision = _revision(old_raw)
            if not isinstance(expected_revision, str) or expected_revision != current_revision:
                raise ConfigurationConflictError("configuration changed externally; reload before applying")
            try:
                current = json.loads(old_raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"current configuration is invalid: {exc}") from exc
            candidate = copy.deepcopy(current)
            changed_paths = set()
            for path, value in changes.items():
                field = CONFIG_FIELD_BY_PATH.get(path)
                if field is None:
                    raise ValueError(f"unknown or read-only configuration field: {path}")
                self._validate_field_value(field, value)
                if _get_path(candidate, path) != value:
                    _set_path(candidate, path, value)
                    changed_paths.add(path)
            if not changed_paths:
                return {**self.read(), "changed": [], "hot_applied": [], "restart_required": []}

            temp_path = self._write_temp(candidate)
            backup_temp = self.backup_path.with_suffix(self.backup_path.suffix + ".tmp")
            hot_paths = {path for path in changed_paths if CONFIG_FIELD_BY_PATH[path].apply_mode == "hot"}
            try:
                with backup_temp.open("wb") as handle:
                    handle.write(old_raw)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(backup_temp, self.backup_path)
                os.replace(temp_path, self.path)
                self._fsync_directory(self.path.parent)
                if live_apply is not None and hot_paths:
                    live_apply(candidate, hot_paths)
            except Exception:
                temp_path.unlink(missing_ok=True)
                backup_temp.unlink(missing_ok=True)
                restore_temp = self.path.with_suffix(self.path.suffix + ".rollback.tmp")
                with restore_temp.open("wb") as handle:
                    handle.write(old_raw)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(restore_temp, self.path)
                self._fsync_directory(self.path.parent)
                if live_apply is not None and hot_paths:
                    try:
                        live_apply(current, hot_paths)
                    except Exception:
                        pass
                raise

            result = self.read()
            result.update({
                "changed": sorted(changed_paths),
                "hot_applied": sorted(hot_paths),
                "restart_required": sorted(changed_paths - hot_paths),
            })
            return result
