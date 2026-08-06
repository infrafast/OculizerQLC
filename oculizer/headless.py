"""Non-interactive Oculizer runtime suitable for service supervision."""

from __future__ import annotations

import logging
import threading

from oculizer.automatic import AutomaticSceneRouter
from oculizer.control_socket import ControlSocketServer
from oculizer.modulation import FrequencyBandModulator, MasterModulator
from oculizer.runtime_control import RuntimeControl


logger = logging.getLogger(__name__)


class HeadlessOculizerService:
    def __init__(self, oculizer, poll_seconds=0.02, silence_config=None, speech_config=None,
                 master_config=None, frequency_config=None, dynamic_control="off",
                 dynamic_controls=None, off_cache_size=10, scene_max_duration=40.0,
                 control_socket_path=None):
        dynamic_controls = dynamic_controls or {}
        profile = ({"cache": off_cache_size, "rate": None, "throttle": None}
                   if dynamic_control == "off" else dynamic_controls[dynamic_control])
        oculizer.set_scene_cache_size(profile["cache"])
        self.oculizer = oculizer
        self.router = AutomaticSceneRouter(
            oculizer,
            silence_config=silence_config,
            speech_config=speech_config,
            scene_rate_limit=profile["rate"],
            scene_throttle=profile["throttle"],
            scene_max_duration=scene_max_duration,
        )
        self.master_modulator = MasterModulator(oculizer, config=master_config)
        self.frequency_modulator = FrequencyBandModulator(oculizer, config=frequency_config)
        self.control = RuntimeControl(
            oculizer, self.router, self.master_modulator, self.frequency_modulator,
            dynamic_controls=dynamic_controls, active_dynamic_control=dynamic_control,
            off_cache_size=off_cache_size, health_check=lambda: self.oculizer.is_alive(),
        )
        self.control_server = ControlSocketServer(control_socket_path, self.control) if control_socket_path else None
        self.poll_seconds = poll_seconds
        self.stop_event = threading.Event()

    def request_stop(self, signum=None, frame=None) -> None:
        if signum is not None:
            logger.info("Received signal %s; stopping", signum)
        self.stop_event.set()

    def run(self) -> int:
        logger.info("Starting non-interactive Oculizer runtime")
        self.master_modulator.startup()
        self.frequency_modulator.startup()
        self.oculizer.start()
        if self.control_server is not None:
            self.control_server.start()
        try:
            while not self.stop_event.wait(self.poll_seconds):
                if not self.oculizer.is_alive():
                    logger.error("Oculizer worker stopped unexpectedly")
                    return 1
                self.control.tick()
            return 0
        finally:
            if self.control_server is not None:
                self.control_server.stop()
            self.master_modulator.shutdown()
            self.frequency_modulator.shutdown()
            self.oculizer.stop()
            self.oculizer.join(timeout=5.0)
            if self.oculizer.is_alive():
                logger.error("Oculizer worker did not stop within five seconds")
            else:
                logger.info("Non-interactive Oculizer runtime stopped")
