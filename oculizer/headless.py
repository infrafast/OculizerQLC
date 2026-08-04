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
                 master_config=None, frequency_config=None, scene_rate_limit=None,
                 scene_throttle=None, presets=None, control_socket_path=None):
        self.oculizer = oculizer
        self.router = AutomaticSceneRouter(
            oculizer,
            silence_config=silence_config,
            speech_config=speech_config,
            scene_rate_limit=scene_rate_limit,
            scene_throttle=scene_throttle,
        )
        self.master_modulator = MasterModulator(oculizer, config=master_config)
        self.frequency_modulator = FrequencyBandModulator(oculizer, config=frequency_config)
        self.control = RuntimeControl(
            oculizer, self.router, self.master_modulator, self.frequency_modulator,
            presets=presets, health_check=lambda: self.oculizer.is_alive(),
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
