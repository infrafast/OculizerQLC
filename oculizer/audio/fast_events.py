"""Small event records used by automatic priority routing."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FastEventType(str, Enum):
    SUDDEN_SILENCE = "SUDDEN_SILENCE"
    AUDIO_RESUME = "AUDIO_RESUME"
    SPEECH_START = "SPEECH_START"
    SPEECH_END = "SPEECH_END"


@dataclass(frozen=True)
class FastAudioEvent:
    type: FastEventType
    timestamp: float
    confidence: float = 0.0
