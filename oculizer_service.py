"""Headless Oculizer entry point for development and future systemd use."""

import argparse
import logging
import signal
import sys
import re

from oculizer.headless import HeadlessOculizerService
from oculizer.control_socket import default_control_socket_path
from oculizer.light import Oculizer, OUTPUT_CHOICES
from oculizer.runtime_config import configured_audio_input, configured_frequency_modulation, configured_master_modulation, configured_prediction, configured_scene_presets, configured_silence, configured_speech, load_runtime_config
from oculizer.scenes import SceneManager


def parse_scene_rate_limit(value):
    match = re.fullmatch(r"([1-9][0-9]*)/([0-9]+(?:\.[0-9]+)?)", value.strip())
    if match is None:
        raise argparse.ArgumentTypeError("expected COUNT/SECONDS, for example 4/5")
    count, seconds = int(match.group(1)), float(match.group(2))
    if not 1 <= count <= 100 or not 0.5 <= seconds <= 300:
        raise argparse.ArgumentTypeError("count must be 1-100 and seconds 0.5-300")
    return count, seconds


def configure_service_streams() -> None:
    """Use explicit carriage-return line endings in terminal service output."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(newline="\r\n", line_buffering=True)


def parse_args():
    parser = argparse.ArgumentParser(description="Run Oculizer without a terminal interface")
    parser.add_argument("--config", default=None, help="General configuration (default: config/oculizer.json)")
    parser.add_argument("--output", choices=OUTPUT_CHOICES, default="qlc-osc")
    parser.add_argument("--profile", default=None, help="Fixture profile required only for Enttec")
    parser.add_argument("--input-device", default=None, help="Prediction audio input selector")
    parser.add_argument("--audio-file", default=None, help="Loop a local PCM WAV file instead of opening an audio device")
    parser.add_argument("--prediction-device", default=None, help="Optional separate prediction input")
    parser.add_argument("--prediction-channels", default=None)
    from oculizer.scene_predictors import list_available_versions
    parser.add_argument(
        "--predictor-version",
        choices=list_available_versions(),
        default="v6",
        help="Scene predictor version to use (default: v6)",
    )
    parser.add_argument("--scene-cache-size", type=int, default=10,
                        help="Number of recent predictions used for smoothing (default: 10)")
    parser.add_argument("--scene-rate-limit", type=parse_scene_rate_limit, default=None, metavar="MAX/SECONDS",
                        help="Limit automatic music scene changes in a rolling window, e.g. 4/5 (default: disabled)")
    parser.add_argument("--scene-throttle", type=parse_scene_rate_limit, default=None, metavar="BURST/RECOVERY_SECONDS",
                        help="Allow a burst then recover one automatic music change credit per interval, e.g. 3/2 (default: disabled)")
    parser.add_argument("--scene-max-duration", type=float, default=40.0, metavar="SECONDS",
                        help="Base automatic music-scene duration before ±30% variation (default: 40 seconds)")
    parser.add_argument("--control-socket", default=default_control_socket_path(), help="Unix runtime control socket path")
    parser.add_argument("--no-control-socket", action="store_true", help="Disable the local runtime control socket")
    parser.add_argument("--qlc-config", default=None, help="Unified QLC+ configuration (default: config/qlc_config.json)")
    parser.add_argument("--osc-host", default=None)
    parser.add_argument("--osc-port", type=int, default=None)
    parser.add_argument("--osc-dry-run", action="store_true", default=None)
    parser.add_argument(
        "--dmx-dry-run",
        action="store_true",
        help="Render Enttec DMX frames through a rate-limited virtual controller",
    )
    parser.add_argument(
        "--filter-dmx", "--filter-DMX",
        action="store_true",
        help="Hide all virtual DMX frame summaries from logs",
    )
    parser.add_argument(
        "--filter-osc",
        action="append",
        default=[],
        metavar="PATH",
        help="Hide one exact OSC path from dry-run logs; repeat for multiple paths",
    )
    args = parser.parse_args()

    try:
        config = load_runtime_config(args.config)
    except ValueError as exc:
        parser.error(str(exc))
    if args.input_device is None:
        args.input_device = configured_audio_input(config)
    args.silence_config = configured_silence(config)
    args.speech_config = configured_speech(config)
    args.prediction_config = configured_prediction(config)
    args.master_config = configured_master_modulation(config)
    args.frequency_config = configured_frequency_modulation(config)
    args.scene_presets = configured_scene_presets(config, reset_cache_size=args.scene_cache_size)
    if args.output == "enttec" and not args.profile:
        parser.error("--profile is required with --output enttec")
    if args.dmx_dry_run and args.output != "enttec":
        parser.error("--dmx-dry-run requires --output enttec")
    if args.filter_dmx and not args.dmx_dry_run:
        parser.error("--filter-dmx requires --dmx-dry-run")
    if args.audio_file and args.prediction_device:
        parser.error("--audio-file cannot be combined with --prediction-device")
    if not 1 <= args.scene_cache_size <= 100:
        parser.error("--scene-cache-size must be between 1 and 100")
    if not 0.5 <= args.scene_max_duration <= 3600:
        parser.error("--scene-max-duration must be between 0.5 and 3600 seconds")
    if isinstance(args.prediction_device, str) and args.prediction_device.isdigit():
        args.prediction_device = int(args.prediction_device)
    return args


def build_service(args) -> HeadlessOculizerService:
    scene_manager = SceneManager(
        "scenes",
        profile_name=args.profile if args.output == "enttec" else None,
    )
    oculizer = Oculizer(
        args.profile,
        scene_manager,
        input_device=args.input_device,
        scene_prediction_enabled=True,
        scene_prediction_device=args.prediction_device,
        predictor_version=args.predictor_version,
        scene_cache_size=args.scene_cache_size,
        prediction_channels=args.prediction_channels,
        output=args.output,
        qlc_config_path=args.qlc_config,
        osc_host=args.osc_host,
        osc_port=args.osc_port,
        osc_dry_run=args.osc_dry_run,
        osc_log_filters=args.filter_osc,
        dmx_dry_run=args.dmx_dry_run,
        filter_dmx=args.filter_dmx,
        prediction_window_seconds=args.prediction_config.window_seconds,
        audio_file=args.audio_file,
    )
    oculizer.restrict_scenes_to_backend()
    return HeadlessOculizerService(
        oculizer,
        silence_config=args.silence_config,
        speech_config=args.speech_config,
        master_config=args.master_config,
        frequency_config=args.frequency_config,
        scene_rate_limit=args.scene_rate_limit,
        scene_throttle=args.scene_throttle,
        scene_max_duration=args.scene_max_duration,
        presets=args.scene_presets,
        control_socket_path=None if args.no_control_socket else args.control_socket,
    )


def main() -> int:
    args = parse_args()
    configure_service_streams()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    service = build_service(args)
    signal.signal(signal.SIGTERM, service.request_stop)
    signal.signal(signal.SIGINT, service.request_stop)
    return service.run()


if __name__ == "__main__":
    raise SystemExit(main())
