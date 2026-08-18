import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

from oculizer.audio.sources import SoundDeviceAudioSource


ROOT = Path(__file__).resolve().parents[1]


class SoundDeviceShutdownLifecycleTests(unittest.TestCase):
    def make_source(self, **kwargs):
        return SoundDeviceAudioSource(
            device=3,
            channels=1,
            sample_rate=48000,
            block_size=1024,
            callback=Mock(),
            **kwargs,
        )

    def test_stop_request_interrupts_capture_without_closing_from_caller(self):
        interrupted = threading.Event()
        stream = Mock()
        stream.active = True
        stream.abort.side_effect = interrupted.set
        source = self.make_source()
        source.stream = stream

        source.request_stop()

        self.assertTrue(interrupted.wait(0.5))
        stream.abort.assert_called_once_with()
        stream.stop.assert_not_called()
        stream.close.assert_not_called()
        self.assertIs(source.stream, stream)

        source.stop()
        stream.close.assert_called_once_with()
        stream.stop.assert_not_called()
        self.assertIsNone(source.stream)

    def test_blocked_close_is_bounded_and_reports_native_stage(self):
        close_started = threading.Event()
        release_close = threading.Event()
        stream = Mock()
        stream.active = True

        def blocked_close():
            close_started.set()
            release_close.wait(1.0)

        stream.close.side_effect = blocked_close
        source = self.make_source(shutdown_timeout=0.05)
        source.stream = stream

        started = time.monotonic()
        with self.assertLogs("oculizer.audio.sources", level="ERROR") as logs:
            source.stop()
        elapsed = time.monotonic() - started

        self.assertTrue(close_started.is_set())
        self.assertLess(elapsed, 0.5)
        self.assertIsNone(source.stream)
        self.assertTrue(any("stream.close() did not finish" in line for line in logs.output))

        release_close.set()

    def test_blocked_abort_is_bounded_without_starting_a_competing_close(self):
        abort_started = threading.Event()
        release_abort = threading.Event()
        stream = Mock()
        stream.active = True

        def blocked_abort():
            abort_started.set()
            release_abort.wait(1.0)

        stream.abort.side_effect = blocked_abort
        source = self.make_source(interrupt_timeout=0.05, shutdown_timeout=0.05)
        source.stream = stream
        source.request_stop()
        self.assertTrue(abort_started.wait(0.5))

        started = time.monotonic()
        with self.assertLogs("oculizer.audio.sources", level="ERROR") as logs:
            source.stop()
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.5)
        self.assertIsNone(source.stream)
        stream.stop.assert_not_called()
        stream.close.assert_not_called()
        self.assertTrue(any("stream.abort() did not finish" in line for line in logs.output))

        release_abort.set()


class RaspberryServiceShutdownOrderingTests(unittest.TestCase):
    def test_service_stops_before_the_service_users_audio_session(self):
        unit = (ROOT / "raspi_service_pack/systemd/oculizer.service").read_text(encoding="utf-8")
        rendered = unit.replace("@SERVICE_UID@", "1000")

        self.assertIn("After=network.target sound.target user@1000.service", rendered)
        self.assertIn("Wants=network.target sound.target user@1000.service", rendered)
        self.assertIn("TimeoutStopSec=30", unit)
        self.assertIn("KillSignal=SIGTERM", unit)


if __name__ == "__main__":
    unittest.main()
