"""Non-interactive Oculizer runtime suitable for service supervision."""

from __future__ import annotations

import logging
import threading

from oculizer.automatic import AutomaticSceneRouter


logger = logging.getLogger(__name__)


class HeadlessOculizerService:
    def __init__(self, oculizer, poll_seconds: float = 0.1, silence_config=None):
        self.oculizer = oculizer
        self.router = AutomaticSceneRouter(oculizer, silence_config=silence_config)
        self.poll_seconds = poll_seconds
        self.stop_event = threading.Event()

    def request_stop(self, signum=None, frame=None) -> None:
        if signum is not None:
            logger.info("Received signal %s; stopping", signum)
        self.stop_event.set()

    def run(self) -> int:
        logger.info("Starting non-interactive Oculizer runtime")
        self.oculizer.start()
        try:
            while not self.stop_event.wait(self.poll_seconds):
                if not self.oculizer.is_alive():
                    logger.error("Oculizer worker stopped unexpectedly")
                    return 1
                self.router.step()
            return 0
        finally:
            self.oculizer.stop()
            self.oculizer.join(timeout=5.0)
            if self.oculizer.is_alive():
                logger.error("Oculizer worker did not stop within five seconds")
            else:
                logger.info("Non-interactive Oculizer runtime stopped")
