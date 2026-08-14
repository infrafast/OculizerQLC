#!/usr/bin/env python3
"""Build the Phase 9 native-only lighting configuration from legacy sources."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_APPLICATION_CONFIG = ROOT / "config/oculizer.json"
DEFAULT_QLC_CONFIG = ROOT / "config/qlc_config.json"
DEFAULT_SCENES = ROOT / "scenes"
BEHAVIORS = {"static", "normal", "responsive"}
NORMAL_DESCRIPTION_TERMS = (
    "alternate", "cycle", "echo", "fade", "flicker", "moving", "movement",
    "pulse", "random", "riser", "sine", "strobe", "wave",
)


def classify_scene(scene: dict[str, Any]) -> str:
    """Propose design metadata from legacy behavior, never runtime policy."""
    lights = scene.get("lights", [])
    modulators = {
        light.get("modulator")
        for light in lights
        if isinstance(light, dict) and light.get("modulator")
    }
    if "mfft" in modulators or isinstance(scene.get("orchestrator"), dict):
        return "responsive"
    description = str(scene.get("description", "")).lower()
    if "time" in modulators or any(term in description for term in NORMAL_DESCRIPTION_TERMS):
        return "normal"
    return "static"


def build_scene_metadata(scenes_directory: Path) -> dict[str, dict[str, Any]]:
    metadata = {}
    for path in sorted(scenes_directory.glob("*.json")):
        scene = json.loads(path.read_text(encoding="utf-8"))
        name = scene.get("name")
        description = scene.get("description")
        if name != path.stem:
            raise ValueError(f"Scene {path} has name {name!r}; expected {path.stem!r}")
        if not isinstance(description, str) or not description.strip():
            raise ValueError(f"Scene {path} has no user-facing description")
        entry: dict[str, Any] = {
            "description": description.strip(),
            "design_behavior": classify_scene(scene),
        }
        if "max_duration_seconds" in scene:
            entry["max_duration_seconds"] = scene["max_duration_seconds"]
        metadata[name] = entry
    if len(metadata) != 127:
        raise ValueError(f"Expected 127 legacy scenes, found {len(metadata)}")
    return metadata


def build_lighting_config(legacy: dict[str, Any], scenes_directory: Path) -> dict[str, Any]:
    routing = legacy["routing"]
    controls = {
        name: value.get("caption", name)
        for name, value in legacy["controls"].items()
    }
    caption_overrides = {
        name: value["caption"]
        for name, value in routing["scenes"].items()
        if value.get("caption", name) != name
    }
    lighting = {
        "native": legacy["native"],
        "controls": controls,
        "routing": {
            "pulse_seconds": routing.get("pulse_seconds", 0.1),
            "fallback_scene": routing.get("fallback_scene"),
            "caption_overrides": caption_overrides,
        },
        "scene_metadata": build_scene_metadata(scenes_directory),
    }
    if lighting["scene_metadata"] and not all(
        entry["design_behavior"] in BEHAVIORS
        for entry in lighting["scene_metadata"].values()
    ):
        raise ValueError("Generated an unsupported scene design behavior")
    return lighting


def migrated_config(application_path: Path, qlc_path: Path, scenes_directory: Path):
    application = json.loads(application_path.read_text(encoding="utf-8"))
    legacy = json.loads(qlc_path.read_text(encoding="utf-8"))
    application["lighting"] = build_lighting_config(legacy, scenes_directory)
    return application


def write_atomic(path: Path, data: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_APPLICATION_CONFIG)
    parser.add_argument("--qlc-config", type=Path, default=DEFAULT_QLC_CONFIG)
    parser.add_argument("--scenes", type=Path, default=DEFAULT_SCENES)
    parser.add_argument("--check", action="store_true", help="Validate without writing")
    args = parser.parse_args(argv)
    result = migrated_config(args.config, args.qlc_config, args.scenes)
    if not args.check:
        write_atomic(args.config, result)
    metadata = result["lighting"]["scene_metadata"]
    counts = {behavior: 0 for behavior in sorted(BEHAVIORS)}
    for entry in metadata.values():
        counts[entry["design_behavior"]] += 1
    durations = sum("max_duration_seconds" in entry for entry in metadata.values())
    print(f"Validated {len(metadata)} scene descriptions, {durations} durations, behaviors={counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
