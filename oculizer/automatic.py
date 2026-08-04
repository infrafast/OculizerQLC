"""Automatic prediction-to-scene routing independent of any user interface."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque

from oculizer.runtime_config import SilenceConfig, SpeechConfig


logger = logging.getLogger(__name__)
_UNCHANGED = object()


class PolicyConflictError(RuntimeError):
    """Raised when a stale editor attempts to replace newer policy state."""


def _synchronized(method):
    def locked(self, *args, **kwargs):
        with self.route_lock:
            return method(self, *args, **kwargs)
    return locked


class AutomaticSceneRouter:
    """Apply stable predictions or a manual override through one scene API."""

    def __init__(self, oculizer, silence_config=None, speech_config=None, clock=None,
                 scene_rate_limit=None, scene_throttle=None):
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
        self._validate_policy(scene_rate_limit, "scene rate limit")
        self._validate_policy(scene_throttle, "scene throttle")
        self.scene_rate_limit = scene_rate_limit
        self.scene_throttle = scene_throttle
        self.automatic_change_times = deque()
        self.throttle_tokens = float(scene_throttle[0]) if scene_throttle is not None else None
        self.throttle_updated_at = self.clock()
        self.last_limited_target = None
        self.priority_route_pending = False
        self.route_lock = threading.RLock()
        self.policy_revision = 0

    @staticmethod
    def _validate_policy(value, label):
        if value is None:
            return
        if (
            not isinstance(value, tuple)
            or len(value) != 2
            or isinstance(value[0], bool)
            or not isinstance(value[0], int)
            or not 1 <= value[0] <= 100
            or not isinstance(value[1], (int, float))
            or isinstance(value[1], bool)
            or not 0.5 <= value[1] <= 300
        ):
            raise ValueError(f"{label} requires count 1-100 and seconds 0.5-300, or Off")

    @_synchronized
    def configure_transition_policy(self, scene_rate_limit=_UNCHANGED,
                                    scene_throttle=_UNCHANGED,
                                    scene_cache_size=_UNCHANGED,
                                    expected_revision=None):
        """Atomically validate and apply live routing/prediction controls."""
        if expected_revision is not None and expected_revision != self.policy_revision:
            raise PolicyConflictError(
                f"scene controls changed externally (expected revision {expected_revision}, "
                f"current {self.policy_revision})"
            )
        if scene_rate_limit is not _UNCHANGED:
            self._validate_policy(scene_rate_limit, "scene rate limit")
        if scene_throttle is not _UNCHANGED:
            self._validate_policy(scene_throttle, "scene throttle")
        if scene_cache_size is not _UNCHANGED:
            if (
                isinstance(scene_cache_size, bool)
                or not isinstance(scene_cache_size, int)
                or not 1 <= scene_cache_size <= 100
            ):
                raise ValueError("scene cache size must be between 1 and 100")

        changed = False
        if scene_cache_size is not _UNCHANGED and scene_cache_size != self.oculizer.scene_cache_size:
            self.oculizer.set_scene_cache_size(scene_cache_size)
            changed = True
        if scene_rate_limit is not _UNCHANGED and scene_rate_limit != self.scene_rate_limit:
            self.scene_rate_limit = scene_rate_limit
            self.automatic_change_times.clear()
            changed = True
        if scene_throttle is not _UNCHANGED and scene_throttle != self.scene_throttle:
            self.scene_throttle = scene_throttle
            self.throttle_tokens = float(scene_throttle[0]) if scene_throttle else None
            self.throttle_updated_at = self.clock()
            changed = True
        if changed:
            self.policy_revision += 1
        self.last_limited_target = None
        logger.info(
            "Live scene controls applied: cache=%d rate=%s throttle=%s",
            self.oculizer.scene_cache_size,
            self.scene_rate_limit or "Off",
            self.scene_throttle or "Off",
        )
        return self.get_transition_policy_status()

    @_synchronized
    def get_transition_policy_status(self):
        """Return a thread-safe snapshot suitable for UI and future sockets."""
        now = self.clock()
        rate_used = 0
        if self.scene_rate_limit is not None:
            maximum, window = self.scene_rate_limit
            cutoff = now - window
            rate_used = sum(timestamp > cutoff for timestamp in self.automatic_change_times)
        tokens = None
        if self.scene_throttle is not None:
            burst, recovery = self.scene_throttle
            tokens = min(burst, self.throttle_tokens + max(0.0, now - self.throttle_updated_at) / recovery)
        return {
            "scene_cache_size": self.oculizer.scene_cache_size,
            "scene_rate_limit": self.scene_rate_limit,
            "scene_rate_used": rate_used,
            "scene_throttle": self.scene_throttle,
            "throttle_tokens": tokens,
            "policy_revision": self.policy_revision,
        }

    def _music_change_allowed(self, target: str, now: float) -> bool:
        """Apply optional final-stage limits to ordinary music transitions."""
        reason = None

        if self.scene_throttle is not None:
            burst, recovery_seconds = self.scene_throttle
            elapsed = max(0.0, now - self.throttle_updated_at)
            self.throttle_tokens = min(
                float(burst),
                self.throttle_tokens + elapsed / recovery_seconds,
            )
            self.throttle_updated_at = now
            if self.throttle_tokens < 1.0:
                reason = f"throttle {burst}/{recovery_seconds:g}s"

        if reason is None and self.scene_rate_limit is not None:
            maximum, window_seconds = self.scene_rate_limit
            cutoff = now - window_seconds
            while self.automatic_change_times and self.automatic_change_times[0] <= cutoff:
                self.automatic_change_times.popleft()
            if len(self.automatic_change_times) >= maximum:
                reason = f"rate limit {maximum}/{window_seconds:g}s"

        if reason is None:
            self.last_limited_target = None
            return True
        limited = (target, reason)
        if limited != self.last_limited_target:
            logger.info("Automatic scene target '%s' held by %s", target, reason)
            self.last_limited_target = limited
        return False

    def _record_automatic_change(self, now: float, rate_limited: bool) -> None:
        if rate_limited:
            if self.scene_rate_limit is not None:
                self.automatic_change_times.append(now)
            if self.scene_throttle is not None:
                self.throttle_tokens = max(0.0, self.throttle_tokens - 1.0)
        self.last_limited_target = None

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

    @_synchronized
    def set_manual_override(self, scene_name: str) -> bool:
        if not self.oculizer.change_scene(scene_name):
            return False
        self.manual_override = scene_name
        self.last_target = self.oculizer.resolve_scene_target(scene_name)
        logger.info("Manual scene override enabled: %s", scene_name)
        return True

    @_synchronized
    def clear_manual_override(self) -> bool:
        if self.manual_override is None:
            return False
        logger.info("Manual scene override cleared: %s", self.manual_override)
        self.manual_override = None
        self.last_target = None
        self.priority_route_pending = True
        return self.step()

    @_synchronized
    def step(self) -> bool:
        requested = self.manual_override
        priority_route = requested is not None or self.priority_route_pending
        silence_was_active = self.silence_active
        if requested is None:
            self._update_silence_state()
        if requested is None and self.silence_active:
            requested = self.silence_config.scene
            priority_route = True
        if requested is None and not self.silence_active:
            speech_was_active = self.speech_active
            semantic_route = self._update_speech_state()
            if semantic_route == "speech":
                requested = self.speech_config.scene
                priority_route = True
            elif semantic_route == "hold":
                return False
            elif speech_was_active:
                # Do not delay return from an announcement once music wins.
                priority_route = True
        if requested is None:
            requested = self.oculizer.current_predicted_scene
            # Resuming from silence is also an immediate safety-state release.
            priority_route = priority_route or silence_was_active
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
        now = self.clock()
        if not priority_route and not self._music_change_allowed(target, now):
            return False
        if not self.oculizer.change_scene(requested):
            return False
        self.last_target = target
        self._record_automatic_change(now, rate_limited=not priority_route)
        self.priority_route_pending = False
        logger.info("Automatic scene request '%s' activated as '%s'", requested, target)
        return True
