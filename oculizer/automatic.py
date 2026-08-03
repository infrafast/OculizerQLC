"""Automatic prediction-to-scene routing independent of any user interface."""

from __future__ import annotations

import logging
import time

from oculizer.runtime_config import SilenceConfig, SpeechConfig


logger = logging.getLogger(__name__)


class AutomaticSceneRouter:
    """Apply stable predictions or a manual override through one scene API."""

    def __init__(self, oculizer, silence_config=None, speech_config=None, clock=None):
        self.oculizer = oculizer
        self.silence_config = silence_config or SilenceConfig(enabled=False)
        self.speech_config = speech_config or SpeechConfig(enabled=False)
        self.clock = clock or time.monotonic
        self.manual_override: str | None = None
        self.last_target: str | None = None
        self.last_rejected: str | None = None
        self.silence_started_at: float | None = None
        self.silence_active = False
        self.speech_active = False
        self.speech_started_at = None
        self.speech_release_at = None

    def _update_speech_state(self) -> str:
        """Return speech, music, or hold after applying semantic hysteresis."""
        if not self.speech_config.enabled:
            return "music"
        scores = getattr(self.oculizer, "current_audioset_scores", None)
        if not scores:
            return "hold"
        now = self.clock()
        speech_score = scores["speech"]
        music_score = scores["music"]
        dominant_speech = (
            speech_score >= self.speech_config.threshold
            and speech_score - music_score >= self.speech_config.music_margin
        )
        dominant_music = (
            music_score >= self.speech_config.threshold
            and music_score - speech_score >= self.speech_config.music_margin
        )
        if dominant_speech:
            self.speech_release_at = None
            if self.speech_started_at is None:
                self.speech_started_at = now
            if not self.speech_active and now - self.speech_started_at >= self.speech_config.minimum_duration_seconds:
                self.speech_active = True
                self.last_target = None
                logger.info("Dominant speech detected: speech=%.3f music=%.3f", speech_score, music_score)
            return "speech" if self.speech_active else "hold"

        if dominant_music:
            self.speech_started_at = None
            if self.speech_active:
                self.speech_release_at = self.speech_release_at or now
                if now - self.speech_release_at >= self.speech_config.release_duration_seconds:
                    self.speech_active = False
                    self.speech_release_at = None
                    self.last_target = None
                    logger.info("Speech routing released")
                    return "music"
                return "speech"
            return "music"

        # Low-confidence or mixed content must not flip between the speech
        # scene and a cluster label. Preserve the current routed state until
        # either speech or music becomes dominant.
        self.speech_release_at = None
        if self.speech_active:
            return "speech"
        self.speech_started_at = None
        return "hold"

    def _set_prediction_suspended(self, suspended: bool) -> None:
        setter = getattr(self.oculizer, "set_prediction_suspended", None)
        if setter is not None:
            setter(suspended)

    def _reset_speech_state(self) -> None:
        self.speech_active = False
        self.speech_started_at = None
        self.speech_release_at = None

    def _update_silence_state(self) -> None:
        if not self.silence_config.enabled:
            return
        rms = getattr(self.oculizer, "current_audio_rms", None)
        if rms is None:
            return
        now = self.clock()
        if self.silence_active:
            if rms >= self.silence_config.resume_threshold:
                self.silence_active = False
                self.silence_started_at = None
                self.last_target = None
                self._set_prediction_suspended(False)
                self._reset_speech_state()
                logger.info("Audio resumed at RMS %.6f", rms)
            return
        if rms <= self.silence_config.threshold:
            if self.silence_started_at is None:
                self.silence_started_at = now
            if now - self.silence_started_at >= self.silence_config.duration_seconds:
                self.silence_active = True
                self.last_target = None
                self._set_prediction_suspended(True)
                self._reset_speech_state()
                logger.info(
                    "Silence detected at RMS %.6f; requesting scene '%s'",
                    rms,
                    self.silence_config.scene,
                )
        else:
            self.silence_started_at = None

    def set_manual_override(self, scene_name: str) -> bool:
        if not self.oculizer.change_scene(scene_name):
            return False
        self.manual_override = scene_name
        self.last_target = self.oculizer.resolve_scene_target(scene_name)
        logger.info("Manual scene override enabled: %s", scene_name)
        return True

    def clear_manual_override(self) -> bool:
        if self.manual_override is None:
            return False
        logger.info("Manual scene override cleared: %s", self.manual_override)
        self.manual_override = None
        self.last_target = None
        return self.step()

    def step(self) -> bool:
        requested = self.manual_override
        if requested is None:
            self._update_silence_state()
        if requested is None and self.silence_active:
            requested = self.silence_config.scene
        if requested is None and not self.silence_active:
            semantic_route = self._update_speech_state()
            if semantic_route == "speech":
                requested = self.speech_config.scene
            elif semantic_route == "hold":
                return False
        if requested is None:
            requested = self.oculizer.current_predicted_scene
        if not requested:
            return False
        target = self.oculizer.resolve_scene_target(requested)
        if target is None:
            if requested != self.last_rejected:
                logger.warning("Predicted scene '%s' has no available target", requested)
                self.last_rejected = requested
            return False
        self.last_rejected = None
        if target == self.last_target:
            return False
        if not self.oculizer.change_scene(requested):
            return False
        self.last_target = target
        logger.info("Automatic scene request '%s' activated as '%s'", requested, target)
        return True
