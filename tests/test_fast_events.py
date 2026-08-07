import unittest

from oculizer.audio.fast_events import FastAudioEvent, FastEventType


class FastAudioEventTests(unittest.TestCase):
    def test_priority_event_record_is_small_and_explicit(self):
        event = FastAudioEvent(FastEventType.SPEECH_START, 12.5, confidence=0.8)

        self.assertEqual(event.type, FastEventType.SPEECH_START)
        self.assertEqual(event.timestamp, 12.5)
        self.assertEqual(event.confidence, 0.8)


if __name__ == "__main__":
    unittest.main()
