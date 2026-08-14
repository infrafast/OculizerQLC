"""
light_control.py

Description: Real-time audio analysis, prediction, and QLC+ Native intentions.

Author: Landry Bulls
Date: 8/20/24
"""

import numpy as np
import librosa
from oculizer.config import audio_parameters
from oculizer.utils import load_json
import threading
import queue
import time
import logging
from collections import deque
from pathlib import Path
from oculizer.light.native_controller import (
    DisabledLightingController,
    create_native_lighting_controller,
)

logger = logging.getLogger(__name__)


def _terminal_line(message=""):
    """Write a terminal line safely while curses newline translation is active."""
    print(message, end="\r\n", flush=True)


class _LazySoundDevice:
    """Preserve the module seam for tests without importing PortAudio eagerly."""

    def __getattr__(self, name):
        import sounddevice

        return getattr(sounddevice, name)


sd = _LazySoundDevice()

class Oculizer(threading.Thread):
    def __init__(self, scene_manager, input_device='cable',
                 scene_prediction_enabled=False, scene_prediction_device=None, predictor_version='v6',
                 average_dual_channels=False, scene_cache_size=10, prediction_channels=None,
                 test_mode=False, config_path=None, qlc_host=None,
                 qlc_port=None, dry_run=None, prediction_window_seconds=4.0,
                 prediction_interval_seconds=1.0,
                 audio_file=None, fast_detection_config=None, qlc_encryption_key=None):
        threading.Thread.__init__(self)
        self.input_device = str(input_device).strip()
        self.audio_file = Path(audio_file).expanduser().resolve() if audio_file else None
        self.sample_rate = audio_parameters['SAMPLERATE']
        self.block_size = audio_parameters['BLOCKSIZE']
        self.hop_length = audio_parameters['HOP_LENGTH']
        self.channels = 1
        self.average_dual_channels = average_dual_channels
        self.test_mode = test_mode
        self.running = threading.Event()
        self.scene_manager = scene_manager
        # Test mode disables lighting output; all normal operation is native.
        if test_mode:
            self.backend = DisabledLightingController()
            logger.info("Test mode: lighting output disabled")
        else:
            if config_path is None:
                current_dir = Path(__file__).resolve().parent
                config_path = current_dir.parent.parent / 'config' / 'oculizer.json'
            self.backend = create_native_lighting_controller(
                config_path,
                host=qlc_host,
                port=qlc_port,
                dry_run=dry_run,
                encryption_key=qlc_encryption_key,
            )
            logger.info("QLC+ native output initialized; authorization continues asynchronously")
        # Audio is only required for direct reactive rendering or prediction.
        # A manual QLC+ selector must not open an otherwise unused native stream.
        self.audio_processing_enabled = (
            scene_prediction_enabled
            or False
        )
        if self.audio_file is not None and scene_prediction_device is not None:
            raise ValueError("--audio-file cannot be combined with a separate prediction device")
        self.device_idx = (
            self._get_audio_device_idx()
            if self.audio_processing_enabled and self.audio_file is None
            else None
        )
        self.audio_source = None
        self.scene_changed = threading.Event()
        self.scene_changed.set()
        
        # Scene prediction setup
        self.scene_prediction_enabled = scene_prediction_enabled
        # Resolve prediction device (can be string name or integer index)
        self.scene_prediction_device = self._get_prediction_device_idx(scene_prediction_device) if scene_prediction_enabled else None
        self.prediction_audio_sample_rate = 48000 if self.scene_prediction_device is not None else self.sample_rate
        self.predictor_version = predictor_version
        self.scene_cache_size = scene_cache_size
        self.prediction_channels_spec = prediction_channels  # Store the user specification
        self.prediction_window_seconds = float(prediction_window_seconds)
        self.prediction_interval = float(prediction_interval_seconds)
        self.prediction_channel_indices = None  # Will be parsed later
        self.scene_predictor = None
        self.prediction_stream = None
        self.prediction_audio_queue = queue.Queue(maxsize=100)  # Limit queue size
        self.prediction_audio_cache = None  # Will be initialized if needed
        self.scene_cache = None
        self.current_predicted_scene = None
        self.latest_prediction = None  # Store the latest raw prediction
        self.current_cluster = None
        self.current_audioset_scores = None
        self.last_prediction_time = 0
        self.prediction_count = 0
        self.prediction_thread = None  # Separate thread for prediction processing
        self.prediction_lock = threading.Lock()  # Lock for thread-safe access
        self.prediction_suspended = threading.Event()
        self.fast_detection_config = fast_detection_config
        self.current_fast_audioset_scores = None
        self.last_fast_semantic_time = 0.0
        self.last_fast_semantic_duration = None
        
        # Audio quality monitoring
        self.audio_quality_check_interval = 100  # Check every N callbacks
        self.audio_callback_count = 0
        self.last_audio_rms = None
        self.current_audio_rms = None
        self.current_mel_spectrum = None

        self.current_mel_sample_rate = None
        self.audio_underrun_count = 0
        self.max_queue_depth_seen = 0  # Track maximum queue buildup
        self.queue_pressure_checks = 0
        self.queue_pressure_level = None
        self.audio_loop_generation = 0

        if self.audio_file is not None:
            from oculizer.audio.sources import WavFileAudioSource

            self.audio_source = WavFileAudioSource(
                self.audio_file,
                self.audio_callback,
                self.block_size,
                on_loop=self._reset_file_loop_state,
            )
            self.capture_sample_rate = self.audio_source.sample_rate
            self.audio_source.block_size = max(
                1,
                int(round(
                    self.block_size * self.audio_source.sample_rate / self.sample_rate
                )),
            )
        
        if scene_prediction_enabled:
            self._init_scene_prediction()
        if self.audio_file is not None:
            # Librosa exposes several modules lazily. Resolve the functions on
            # this construction thread before the WAV and UI threads can use
            # them concurrently.
            getattr(librosa, "resample")
            getattr(librosa.feature, "melspectrogram")

    def set_scene_cache_size(self, size):
        """Resize prediction smoothing safely while preserving newest samples."""
        if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= 100:
            raise ValueError("scene cache size must be between 1 and 100")
        with self.prediction_lock:
            self.scene_cache_size = size
            if self.scene_cache is not None:
                self.scene_cache = deque(self.scene_cache, maxlen=size)
                if self.scene_cache:
                    try:
                        self.current_predicted_scene = mode(self.scene_cache)
                    except Exception:
                        self.current_predicted_scene = self.scene_cache[-1]
        logger.info("Scene cache size changed live to %d", size)

    def _get_audio_device_idx(self):
        devices = sd.query_devices()
        selector = self.input_device.casefold()

        if selector == 'default':
            try:
                default_input = sd.query_devices(kind='input')['index']
            except (sd.PortAudioError, ValueError):
                default_input = -1
            if 0 <= default_input < len(devices) and devices[default_input]['max_input_channels'] > 0:
                return default_input
            raise ValueError("The operating system does not expose a default audio input device.")

        if selector.isdigit():
            device_idx = int(selector)
            if 0 <= device_idx < len(devices) and devices[device_idx]['max_input_channels'] > 0:
                return device_idx
            raise ValueError(f"Audio device index {device_idx} is not a valid input device.")

        aliases = {
            'blackhole': ('blackhole',),
            'scarlett': ('scarlett', 'focusrite'),
            'cable': ('cable',),
            'cable_input': ('cable input',),
            'cable_output': ('cable output',),
        }
        search_terms = aliases.get(selector, (selector,))
        input_devices = [
            (i, device) for i, device in enumerate(devices)
            if device['max_input_channels'] > 0
        ]

        # Prefer an exact device name before accepting a portable substring.
        for i, device in input_devices:
            if device['name'].casefold() == selector:
                return i
        for i, device in input_devices:
            device_name = device['name'].casefold()
            if any(term in device_name for term in search_terms):
                return i
        
        # If device not found, print available devices and raise error
        print("\nAvailable audio input devices:")
        for i, device in enumerate(devices):
            if device['max_input_channels'] > 0:  # Only show input devices
                print(f"{i}: {device['name']}")
        
        raise ValueError(f"Audio input device '{self.input_device}' not found. Please check available devices above.")

    def _parse_channel_spec(self, channel_spec, max_channels):
        """
        Parse channel specification string into list of 0-based indices.
        
        Examples:
            "1" -> [0] (channel 1)
            "1,2" -> [0, 1] (channels 1-2)
            "1-4" -> [0, 1, 2, 3] (channels 1-4)
            "1-16" -> [0, 1, ..., 15] (all 16 channels)
        
        Args:
            channel_spec: String specification of channels (1-based)
            max_channels: Maximum number of channels available
            
        Returns:
            List of 0-based channel indices
        """
        import logging
        logger = logging.getLogger(__name__)
        
        try:
            indices = []
            
            # Handle range notation (e.g., "1-16")
            if '-' in channel_spec:
                start, end = channel_spec.split('-')
                start_idx = int(start) - 1  # Convert to 0-based
                end_idx = int(end) - 1
                
                if start_idx < 0 or end_idx >= max_channels:
                    logger.warning(f"Channel range {channel_spec} out of bounds (1-{max_channels})")
                    return None
                
                indices = list(range(start_idx, end_idx + 1))
                
            # Handle comma-separated list (e.g., "1,2,3")
            elif ',' in channel_spec:
                for ch in channel_spec.split(','):
                    ch_idx = int(ch.strip()) - 1  # Convert to 0-based
                    if ch_idx < 0 or ch_idx >= max_channels:
                        logger.warning(f"Channel {int(ch.strip())} out of bounds (1-{max_channels})")
                        return None
                    indices.append(ch_idx)
                    
            # Handle single channel (e.g., "1")
            else:
                ch_idx = int(channel_spec) - 1  # Convert to 0-based
                if ch_idx < 0 or ch_idx >= max_channels:
                    logger.warning(f"Channel {channel_spec} out of bounds (1-{max_channels})")
                    return None
                indices = [ch_idx]
            
            logger.info(f"Using prediction channels: {[i+1 for i in indices]} (user-specified)")
            return indices
            
        except Exception as e:
            logger.error(f"Error parsing channel specification '{channel_spec}': {e}")
            return None
    
    def _get_prediction_device_idx(self, device_spec):
        """Get device index for prediction, handling both string names and integer indices."""
        if device_spec is None:
            return None
        
        # If it's already an integer, try to use it directly but validate
        if isinstance(device_spec, int):
            devices = sd.query_devices()
            if 0 <= device_spec < len(devices):
                return device_spec
            else:
                print(f"\nWarning: Device index {device_spec} is out of range.")
                # Fall back to searching for cable_output
                device_spec = 'cable_output'
        
        # If it's a string, search for matching device
        if isinstance(device_spec, str):
            device_name = device_spec.lower()
            devices = sd.query_devices()
            for i, device in enumerate(devices):
                if device_name == 'blackhole' and 'BlackHole' in device['name'] and device['max_input_channels'] > 0:
                    return i
                elif device_name == 'scarlett' and ('Scarlett' in device['name'] or 'Focusrite' in device['name']) and device['max_input_channels'] > 0:
                    return i
                elif device_name == 'cable' and 'CABLE' in device['name'] and device['max_input_channels'] > 0:
                    return i
                elif device_name == 'cable_input' and 'CABLE Input' in device['name'] and device['max_input_channels'] > 0:
                    return i
                elif device_name == 'cable_output' and 'CABLE Output' in device['name'] and device['max_input_channels'] > 0:
                    return i
            
            # If device not found, print available devices and raise error
            print("\nAvailable audio input devices:")
            for i, device in enumerate(devices):
                if device['max_input_channels'] > 0:
                    print(f"{i}: {device['name']}")
            
            raise ValueError(f"Prediction audio device '{device_spec}' not found. Please check available devices above.")
        
        return device_spec

    def _init_scene_prediction(self):
        """Initialize scene prediction components."""
        import contextlib
        import io
        from oculizer.scene_predictors import get_predictor
        from collections import deque
        import librosa
        
        # Get the ScenePredictor class for the specified version
        ScenePredictor = get_predictor(self.predictor_version)
        
        # Different predictor versions use different sample rates
        # Both supported predictors were trained at 48 kHz.
        self.prediction_sr = 48000
        
        # Historical predictor implementations and EfficientAT print model
        # internals directly. Capture that output so service logs stay concise.
        captured_output = io.StringIO()
        logger.info("Initializing %s scene predictor", self.predictor_version)
        try:
            with contextlib.redirect_stdout(captured_output), contextlib.redirect_stderr(captured_output):
                self.scene_predictor = ScenePredictor(sr=self.prediction_sr)
        except Exception:
            captured = captured_output.getvalue().strip()
            if captured:
                logger.error("Predictor initialization output:\n%s", captured)
            raise
        captured = captured_output.getvalue().strip()
        if captured:
            logger.debug("Suppressed predictor initialization output:\n%s", captured)
        
        # Cache source-rate audio; resampling happens immediately before inference.
        self.prediction_audio_cache = deque(
            maxlen=int(self.prediction_audio_sample_rate * self.prediction_window_seconds)
        )
        
        # Initialize scene cache with configurable size
        self.scene_cache = deque(maxlen=self.scene_cache_size)
        
        logger.info(
            "Scene prediction initialized with %s predictor at %dHz (device: %s); "
            "window %.1fs, interval %.1fs",
            self.predictor_version,
            self.prediction_sr,
            self.scene_prediction_device,
            self.prediction_window_seconds,
            self.prediction_interval,
        )
        logger.info(
            "Scene cache size: %d (%s)",
            self.scene_cache_size,
            "one prediction" if self.scene_cache_size == 1 else
            f"~{self.scene_cache_size * self.prediction_interval:.1f}s sampled history",
        )
        
        # Validate device sample rate if we have a prediction device
        if self.scene_prediction_device is not None:
            try:
                device_info = sd.query_devices(self.scene_prediction_device)
                device_sr = device_info.get('default_samplerate', 48000)
                device_name = device_info.get('name', 'Unknown')
                logger.info(f"Prediction device '{device_name}' native sample rate: {device_sr}Hz")
                
                # Warn if device sample rate doesn't match what we expect
                if abs(device_sr - 48000) > 100:  # Allow small tolerance
                    logger.warning(f"⚠️  Prediction device sample rate ({device_sr}Hz) differs from expected 48000Hz!")
                    logger.warning("    This may cause audio quality issues. Consider configuring the device to 48kHz.")
            except Exception as e:
                logger.warning(f"Could not validate prediction device sample rate: {e}")

    def prediction_audio_callback(self, indata, frames, time_info, status):
        """Callback for scene prediction audio stream."""
        # Check if still running to avoid queue operations after stop
        if not self.running.is_set():
            return
            
        if status:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"⚠️  Prediction audio status: {status}")
            self.audio_underrun_count += 1
        
        # Extract specified channels and convert to mono
        if len(indata.shape) > 1 and self.prediction_channel_indices is not None:
            # Select specified channels and average them
            selected_channels = indata[:, self.prediction_channel_indices]
            if len(self.prediction_channel_indices) > 1:
                mono_data = np.mean(selected_channels, axis=1)
            else:
                mono_data = selected_channels.flatten()
        elif len(indata.shape) > 1:
            # Default: average all channels
            mono_data = np.mean(indata, axis=1)
        else:
            mono_data = indata.flatten()
        self.current_audio_rms = float(np.sqrt(np.mean(mono_data ** 2)))
        
        # Periodic audio quality monitoring
        self.audio_callback_count += 1
        if self.audio_callback_count % self.audio_quality_check_interval == 0:
            import logging
            logger = logging.getLogger(__name__)
            
            # Check RMS energy
            rms = self.current_audio_rms
            
            # Log if RMS is suspiciously low (possible silence/disconnection)
            if rms < 0.001:
                logger.warning(f"⚠️  Very low audio level detected (RMS: {rms:.6f}) - check audio routing")
            
            # Log if RMS changed dramatically (possible sample rate issues)
            if self.last_audio_rms is not None:
                rms_change = abs(rms - self.last_audio_rms) / (self.last_audio_rms + 1e-10)
                if rms_change > 10.0 and rms > 0.001:  # More than 10x change
                    logger.warning(f"⚠️  Large audio level change detected - possible audio quality issue")
            
            self.last_audio_rms = rms
            
            # Report underruns if any occurred
            if self.audio_underrun_count > 0:
                logger.warning(f"⚠️  {self.audio_underrun_count} audio buffer underruns detected in last {self.audio_quality_check_interval} callbacks")
                self.audio_underrun_count = 0
        
        # Add to queue for processing (non-blocking to avoid hanging)
        try:
            self.prediction_audio_queue.put_nowait(mono_data.copy())
        except queue.Full:
            pass  # Drop frame if queue is full to avoid blocking
        
        # In test mode, also compute FFT for visualization
        if self.test_mode:
            try:
                mfft_data = np.mean(librosa.feature.melspectrogram(
                    y=mono_data, 
                    sr=48000,  # Using prediction stream sample rate
                    n_fft=1024,  # Using prediction stream block size
                    hop_length=512
                ), axis=1)
                self.current_mel_spectrum = mfft_data
                self.current_mel_sample_rate = 48000
            except Exception as e:
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f"Error computing FFT in test mode: {e}")

    def prediction_processing_thread(self):
        """Separate thread for processing scene predictions (heavy CPU work)."""
        import librosa
        import logging
        from statistics import mode
        from collections import Counter
        
        logger = logging.getLogger(__name__)
        logger.info("Prediction processing thread started")
        
        while self.running.is_set():
            try:
                if self.prediction_suspended.is_set():
                    while not self.prediction_audio_queue.empty():
                        try:
                            self.prediction_audio_queue.get_nowait()
                        except queue.Empty:
                            break
                    time.sleep(0.1)
                    continue

                # A queue naturally accumulates chunks while inference is
                # running and while waiting for the configured cadence. Only
                # sustained pressure near its bounded capacity indicates lag.
                queue_depth = self.prediction_audio_queue.qsize()
                if queue_depth > self.max_queue_depth_seen:
                    self.max_queue_depth_seen = queue_depth
                queue_capacity = self.prediction_audio_queue.maxsize
                queue_ratio = queue_depth / queue_capacity
                if queue_ratio >= 0.95:
                    self.queue_pressure_checks += 1
                    if self.queue_pressure_level != "critical":
                        logger.error(
                            "🛑 Audio queue critical: %d/%d chunks; real-time audio may be lost",
                            queue_depth,
                            queue_capacity,
                        )
                    self.queue_pressure_level = "critical"
                elif queue_ratio >= 0.80:
                    self.queue_pressure_checks += 1
                    if self.queue_pressure_checks >= 3 and self.queue_pressure_level is None:
                        logger.warning(
                            "⚠️  Sustained audio queue pressure: %d/%d chunks",
                            queue_depth,
                            queue_capacity,
                        )
                        self.queue_pressure_level = "warning"
                else:
                    self.queue_pressure_checks = 0
                    self.queue_pressure_level = None
                
                # Process any queued audio data (with timeout to check running flag)
                try:
                    audio_chunk = self.prediction_audio_queue.get(timeout=0.5)
                    with self.prediction_lock:
                        self.prediction_audio_cache.extend(audio_chunk)
                except queue.Empty:
                    continue
                
                # Check if the configured source-rate window is full.
                with self.prediction_lock:
                    cache_length = len(self.prediction_audio_cache)

                # Speech/music checks run at one fixed, predictable cadence
                # on this thread and the existing EfficientAT model instance.
                fast_config = self.fast_detection_config
                if fast_config is not None and fast_config.enabled and fast_config.speech.enabled:
                    now = time.monotonic()
                    short_samples = int(
                        self.prediction_audio_sample_rate * fast_config.speech.window_seconds
                    )
                    if (
                        cache_length >= short_samples
                        and now - self.last_fast_semantic_time >= fast_config.speech.interval_seconds
                    ):
                        with self.prediction_lock:
                            semantic_audio = np.asarray(self.prediction_audio_cache)[-short_samples:]
                        if self.prediction_audio_sample_rate != self.prediction_sr:
                            semantic_audio = librosa.resample(
                                semantic_audio,
                                orig_sr=self.prediction_audio_sample_rate,
                                target_sr=self.prediction_sr,
                            )
                        semantic_started = time.monotonic()
                        semantic_scores = self.scene_predictor.get_semantic_scores(semantic_audio)
                        semantic_duration = time.monotonic() - semantic_started
                        with self.prediction_lock:
                            self.current_fast_audioset_scores = semantic_scores
                            self.last_fast_semantic_time = time.monotonic()
                            self.last_fast_semantic_duration = semantic_duration
                        logger.debug(
                            "Semantic speech check: %.1fms speech=%.3f music=%.3f",
                            semantic_duration * 1000.0,
                            semantic_scores.get("speech", 0.0),
                            semantic_scores.get("music", 0.0),
                        )
                
                if cache_length < int(self.prediction_audio_sample_rate * self.prediction_window_seconds):
                    continue
                
                # Check if it's time for prediction
                current_time = time.time()
                if current_time - self.last_prediction_time < self.prediction_interval:
                    continue
                
                # FLUSH QUEUE: Drain any accumulated audio to get freshest data
                # This prevents predicting on stale audio if predictions are slow
                chunks_flushed = 0
                while not self.prediction_audio_queue.empty():
                    try:
                        audio_chunk = self.prediction_audio_queue.get_nowait()
                        with self.prediction_lock:
                            self.prediction_audio_cache.extend(audio_chunk)
                        chunks_flushed += 1
                    except queue.Empty:
                        break
                
                # Copy audio data for processing (release lock quickly)
                with self.prediction_lock:
                    audio_data = np.array(self.prediction_audio_cache)
                
                # Log if we flushed a lot of chunks (indicates lag)
                if chunks_flushed > 5:
                    logger.debug(f"Flushed {chunks_flushed} queued audio chunks before prediction")
                
                # Resample if needed (audio stream is at 48kHz, predictor expects self.prediction_sr)
                # Note: In dual-stream mode, prediction device might have different sample rate
                audio_stream_sample_rate = self.prediction_audio_sample_rate
                if audio_stream_sample_rate != self.prediction_sr:
                    audio_data = librosa.resample(
                        audio_data,
                        orig_sr=audio_stream_sample_rate,
                        target_sr=self.prediction_sr
                    )
                
                # Make prediction (heavy CPU work happens here) - measure time
                prediction_start_time = time.time()
                scene, cluster = self.scene_predictor.predict(audio_data, return_cluster=True)
                semantic_scores = getattr(self.scene_predictor, 'last_audioset_scores', None)
                prediction_duration = time.time() - prediction_start_time
                
                # Update state with lock
                with self.prediction_lock:
                    self.latest_prediction = scene  # Store the raw prediction
                    self.scene_cache.append(scene)
                    
                    # Update current scene using mode of recent predictions
                    if self.scene_cache:
                        try:
                            self.current_predicted_scene = mode(self.scene_cache)
                        except:
                            # If no unique mode (tie), use the most recent
                            self.current_predicted_scene = self.scene_cache[-1]
                    
                    self.current_cluster = cluster
                    self.current_audioset_scores = semantic_scores
                    self.last_prediction_time = current_time
                    self.prediction_count += 1
                    
                    # Log prediction periodically with cache info and timing
                    if self.prediction_count % 10 == 0:
                        # Get distribution of scenes in cache for debugging
                        scene_counts = Counter(self.scene_cache)
                        cache_info = f"Cache({len(self.scene_cache)}): {dict(scene_counts)}"
                        
                        # Get current queue depth
                        current_queue_depth = self.prediction_audio_queue.qsize()
                        queue_info = f"Q:{current_queue_depth}"
                        if self.max_queue_depth_seen > 10:
                            queue_info += f"(max:{self.max_queue_depth_seen})"
                        
                        logger.info(
                            f"[{self.prediction_count:04d}] Prediction: {scene}, "
                            f"Mode: {self.current_predicted_scene}, Cluster: {cluster} | "
                            f"Time: {prediction_duration*1000:.1f}ms | {queue_info} | {cache_info}"
                        )
                    
                    # Judge inference time against its configured scheduling
                    # budget instead of the historical fixed 500ms threshold.
                    if prediction_duration >= self.prediction_interval:
                        logger.error(
                            "🛑 Prediction exceeded its %.0fms interval: %.1fms; real-time lag is likely",
                            self.prediction_interval * 1000.0,
                            prediction_duration * 1000.0,
                        )
                    elif prediction_duration >= self.prediction_interval * 0.80:
                        logger.warning(
                            "⚠️  Prediction used %.0f%% of its %.0fms interval: %.1fms",
                            prediction_duration / self.prediction_interval * 100.0,
                            self.prediction_interval * 1000.0,
                            prediction_duration * 1000.0,
                        )
                    
            except Exception as e:
                logger.error(f"Error in prediction processing thread: {e}")
                import traceback
                traceback.print_exc()
                time.sleep(0.5)  # Avoid tight loop on repeated errors
        
        logger.info("Prediction processing thread stopped")

    def reset_prediction_state(self) -> None:
        """Discard scene evidence that must not cross a priority-route boundary."""
        with self.prediction_lock:
            if self.scene_cache is not None:
                self.scene_cache.clear()
            self.current_predicted_scene = None
            self.latest_prediction = None

    def set_prediction_suspended(self, suspended: bool) -> None:
        """Pause heavy inference and discard stale prediction audio during silence."""
        if suspended:
            if self.prediction_suspended.is_set():
                return
            self.prediction_suspended.set()
            with self.prediction_lock:
                if self.prediction_audio_cache is not None:
                    self.prediction_audio_cache.clear()
                if self.scene_cache is not None:
                    self.scene_cache.clear()
                self.current_predicted_scene = None
                self.latest_prediction = None
                self.current_audioset_scores = None
                self.current_fast_audioset_scores = None
            logger.info("Prediction inference suspended")
        else:
            if not self.prediction_suspended.is_set():
                return
            while not self.prediction_audio_queue.empty():
                try:
                    self.prediction_audio_queue.get_nowait()
                except queue.Empty:
                    break
            with self.prediction_lock:
                if self.prediction_audio_cache is not None:
                    self.prediction_audio_cache.clear()
                if self.scene_cache is not None:
                    self.scene_cache.clear()
                self.current_predicted_scene = None
                self.latest_prediction = None
                self.current_audioset_scores = None
                self.current_fast_audioset_scores = None
            self.prediction_suspended.clear()
            self.last_prediction_time = 0
            logger.info("Prediction inference resumed")
    
    def update_scene_prediction(self):
        """Lightweight method called from main thread - just checks thread health."""
        if not self.scene_prediction_enabled:
            return
        
        # Check if prediction thread is still alive
        if self.prediction_thread and not self.prediction_thread.is_alive():
            import logging
            logger = logging.getLogger(__name__)
            logger.warning("Prediction thread died, attempting to restart...")
            self.prediction_thread = threading.Thread(target=self.prediction_processing_thread, daemon=True)
            self.prediction_thread.start()

    def audio_callback(self, indata, frames, time, status):
        if status:
            logger.warning("Audio callback status: %s", status)
            return
        
        # Check if still running to avoid operations after stop
        if not self.running.is_set():
            return
        
        # Handle stereo input - average channels 1 and 2 if dual channel mode is enabled
        if self.average_dual_channels and len(indata.shape) > 1 and indata.shape[1] >= 2:
            # Average the first two channels (0 and 1, which are channels 1 and 2)
            audio_data = np.mean(indata[:, :2], axis=1)
        else:
            audio_data = indata.copy().flatten()

        capture_sample_rate = getattr(self, 'capture_sample_rate', self.sample_rate)
        if capture_sample_rate != self.sample_rate:
            audio_data = librosa.resample(
                audio_data,
                orig_sr=capture_sample_rate,
                target_sr=self.sample_rate,
            )
        self.current_audio_rms = float(np.sqrt(np.mean(audio_data ** 2)))
        
        # In single-stream mode, also feed prediction queue
        if self.scene_prediction_enabled and self.scene_prediction_device is None:
            try:
                self.prediction_audio_queue.put_nowait(audio_data.copy())
            except queue.Full:
                pass  # Drop frame if queue is full to avoid blocking
        
        mfft_data = np.mean(librosa.feature.melspectrogram(y=audio_data, sr=self.sample_rate, n_fft=self.block_size, hop_length=self.hop_length), axis=1)
        self.current_mel_spectrum = mfft_data
        self.current_mel_sample_rate = self.sample_rate

    def _reset_file_loop_state(self):
        """Discard temporal state that must not cross a WAV loop boundary."""
        with self.prediction_lock:
            if self.prediction_audio_cache is not None:
                self.prediction_audio_cache.clear()
            if self.scene_cache is not None:
                self.scene_cache.clear()
            self.current_audioset_scores = None
            self.current_fast_audioset_scores = None
            self.latest_prediction = None
            self.current_predicted_scene = None
            self.current_cluster = None
        while not self.prediction_audio_queue.empty():
            try:
                self.prediction_audio_queue.get_nowait()
            except queue.Empty:
                break
        self.current_audio_rms = None
        self.current_mel_spectrum = None
        self.current_mel_sample_rate = None
        self.audio_loop_generation = getattr(self, "audio_loop_generation", 0) + 1
        logger.info("WAV audio source loop restarted; temporal analysis state reset")

    def run(self):
        self.running.set()
        
        import logging
        logger = logging.getLogger(__name__)

        if not self.audio_processing_enabled:
            logger.info("Audio stream disabled: selected backend has no active audio consumer")
            while self.running.is_set():
                time.sleep(0.1)
            return

        if self.audio_file is not None:
            source = self.audio_source
            if self.scene_prediction_enabled:
                self.prediction_thread = threading.Thread(
                    target=self.prediction_processing_thread, daemon=True
                )
                self.prediction_thread.start()
            logger.info(
                "WAV audio source: '%s' (%d Hz, %d channels, looped)",
                source.path,
                source.sample_rate,
                source.channels,
            )
            source.start()
            try:
                while self.running.is_set() and source.is_alive():
                    self.process_audio_and_lights()
                    if self.scene_prediction_enabled:
                        self.update_scene_prediction()
                    time.sleep(0.001)
                if source.error is not None:
                    raise source.error
            except Exception:
                logger.exception("Error in WAV audio source")
            finally:
                source.stop()
                source.join(timeout=2.0)
            return
        
        # In test mode, skip FFT stream entirely and only run predictions
        if self.test_mode:
            logger.info("Test mode: Skipping FFT/reactivity audio stream")
            try:
                # Only start prediction stream in test mode
                if self.scene_prediction_enabled and self.scene_prediction_device is not None:
                    pred_device_info = sd.query_devices(self.scene_prediction_device)
                    max_input_channels = pred_device_info['max_input_channels']
                    
                    # Parse channel specification
                    if self.prediction_channels_spec:
                        self.prediction_channel_indices = self._parse_channel_spec(
                            self.prediction_channels_spec, max_input_channels
                        )
                        pred_channels = max(self.prediction_channel_indices) + 1  # Open enough channels
                    else:
                        # Auto-detect: for multi-channel devices, open all channels
                        if 'BlackHole' in pred_device_info['name'] and max_input_channels > 2:
                            pred_channels = max_input_channels  # Open all channels
                            self.prediction_channel_indices = None  # Average all
                        elif 'Scarlett' in pred_device_info['name'] or 'CABLE' in pred_device_info['name']:
                            pred_channels = 2
                            self.prediction_channel_indices = None  # Average all
                        else:
                            pred_channels = 1
                            self.prediction_channel_indices = None
                    
                    self.prediction_stream = sd.InputStream(
                        device=self.scene_prediction_device,
                        channels=pred_channels,
                        samplerate=48000,  # Typical for CABLE Output
                        blocksize=1024,
                        callback=self.prediction_audio_callback
                    )
                    self.prediction_stream.start()
                    
                    # Start prediction processing thread
                    self.prediction_thread = threading.Thread(target=self.prediction_processing_thread, daemon=True)
                    self.prediction_thread.start()
                    
                    pred_device_name = pred_device_info['name']
                    if self.prediction_channel_indices:
                        channels_desc = f"channels {[i+1 for i in self.prediction_channel_indices]} (averaged)"
                    else:
                        channels_desc = f"all {pred_channels} channels (averaged)"
                    logger.info(f"🎵 Scene Prediction: '{pred_device_name}' - using {channels_desc} at 48000Hz")
                    
                    # In test mode, main loop only handles predictions
                    while self.running.is_set():
                        if self.scene_prediction_enabled:
                            self.update_scene_prediction()
                        time.sleep(0.1)
                else:
                    logger.error("Test mode requires scene_prediction_enabled and scene_prediction_device")
                    return
                    
            except Exception:
                logger.exception("Error in test mode")
            finally:
                # Clean up prediction stream
                if self.prediction_stream:
                    try:
                        self.prediction_stream.stop()
                        self.prediction_stream.close()
                        self.prediction_stream = None
                    except Exception as e:
                        logger.error(f"Error closing prediction stream: {e}")
            return
        
        # Normal mode: Determine channels for main FFT stream
        # Use 2 channels if average_dual_channels is enabled, otherwise use default
        device_info = sd.query_devices(self.device_idx)
        main_channels = 2 if self.average_dual_channels else self.channels
        self.capture_sample_rate = int(round(device_info['default_samplerate']))
        capture_block_size = max(
            1,
            int(round(self.block_size * self.capture_sample_rate / self.sample_rate)),
        )
        
        # Log FFT/reactivity stream configuration
        fft_device_name = device_info['name']
        if self.average_dual_channels:
            logger.info(
                "FFT/Reactivity: '%s' - averaging channels 1-2 at %dHz, analysis at %dHz",
                fft_device_name,
                self.capture_sample_rate,
                self.sample_rate,
            )
        else:
            logger.info(
                "FFT/Reactivity: '%s' - channel 1 at %dHz, analysis at %dHz",
                fft_device_name,
                self.capture_sample_rate,
                self.sample_rate,
            )
        
        try:
            # Start the shared audio stream for analysis and prediction.
            from oculizer.audio.sources import SoundDeviceAudioSource

            with SoundDeviceAudioSource(
                device=self.device_idx,
                channels=main_channels,
                sample_rate=self.capture_sample_rate,
                block_size=capture_block_size,
                callback=self.audio_callback
            ) as source:
                self.audio_source = source
                # Start scene prediction stream if enabled and separate device specified
                if self.scene_prediction_enabled and self.scene_prediction_device is not None:
                    pred_device_info = sd.query_devices(self.scene_prediction_device)
                    max_input_channels = pred_device_info['max_input_channels']
                    
                    # Parse channel specification
                    if self.prediction_channels_spec:
                        self.prediction_channel_indices = self._parse_channel_spec(
                            self.prediction_channels_spec, max_input_channels
                        )
                        pred_channels = max(self.prediction_channel_indices) + 1  # Open enough channels
                    else:
                        # Auto-detect: for multi-channel devices, open all channels
                        if 'BlackHole' in pred_device_info['name'] and max_input_channels > 2:
                            pred_channels = max_input_channels  # Open all channels
                            self.prediction_channel_indices = None  # Average all
                        elif 'Scarlett' in pred_device_info['name'] or 'CABLE' in pred_device_info['name']:
                            pred_channels = 2
                            self.prediction_channel_indices = None  # Average all
                        else:
                            pred_channels = 1
                            self.prediction_channel_indices = None
                    
                    self.prediction_stream = sd.InputStream(
                        device=self.scene_prediction_device,
                        channels=pred_channels,
                        samplerate=48000,  # Typical for CABLE Output
                        blocksize=1024,
                        callback=self.prediction_audio_callback
                    )
                    self.prediction_stream.start()
                    
                    # Start prediction processing thread
                    self.prediction_thread = threading.Thread(target=self.prediction_processing_thread, daemon=True)
                    self.prediction_thread.start()
                    
                    import logging
                    logger = logging.getLogger(__name__)
                    pred_device_name = pred_device_info['name']
                    if self.prediction_channel_indices:
                        channels_desc = f"channels {[i+1 for i in self.prediction_channel_indices]} (averaged)"
                    else:
                        channels_desc = f"all {pred_channels} channels (averaged)"
                    logger.info(f"🎵 Scene Prediction: '{pred_device_name}' - using {channels_desc} at 48000Hz")
                
                # Start prediction thread for single-stream mode if enabled
                if self.scene_prediction_enabled and self.scene_prediction_device is None:
                    self.prediction_thread = threading.Thread(target=self.prediction_processing_thread, daemon=True)
                    self.prediction_thread.start()
                    
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.info(f"🎵 Scene Prediction: Single-stream mode (sharing FFT device)")
                
                # Main processing loop
                while self.running.is_set():
                    self.process_audio_and_lights()
                    
                    # Update scene prediction periodically
                    if self.scene_prediction_enabled:
                        self.update_scene_prediction()
                    
                    time.sleep(0.001)
                    
        except Exception:
            logger.exception("Error in audio stream")
        finally:
            # Clean up prediction stream if not already stopped
            if self.prediction_stream:
                try:
                    self.prediction_stream.stop()
                    self.prediction_stream.close()
                    self.prediction_stream = None
                except Exception:
                    logger.exception("Error closing prediction stream")

    def process_audio_and_lights(self):
        # QLC+ owns rendering; audio callbacks feed prediction/modulation only.
        self.scene_changed.clear()

    def change_scene(self, scene_name):
        # The backend owns output-state transitions. If activation fails (for
        # example, because the logical scene is unmapped), preserve the current
        # logical scene state and report the failed command to the caller.
        target_scene = self.resolve_scene_target(scene_name)
        if target_scene is None:
            logger.warning("Requested scene '%s' has no available output target", scene_name)
            return False
        if not self.backend.activate_scene(target_scene):
            return False
        # Reset all effect states before changing scene
        self.scene_manager.set_scene(target_scene, apply_fallback=False)
        # Set flag for main loop to handle the transition
        self.scene_changed.set()
        logger.info("Scene request '%s' activated as '%s'", scene_name, target_scene)
        # Main loop will turn off lights and apply new scene
        return True

    def resolve_scene_target(self, scene_name):
        """Resolve backend routing and logical catalog membership."""
        backend_target = self.backend.resolve_scene(scene_name)
        if backend_target is None:
            return None
        if backend_target not in self.scene_manager.scenes:
            return None
        return self.scene_manager.resolve_scene(backend_target, apply_fallback=False)

    def get_scene_max_duration(self, scene_name):
        """Return a scene-specific automatic duration override, if declared."""
        metadata = getattr(self.backend, "scene_metadata", {}).get(scene_name)
        if isinstance(metadata, dict):
            return metadata.get("max_duration_seconds")
        scene = self.scene_manager.scenes.get(scene_name)
        if not isinstance(scene, dict):
            return None
        return scene.get("max_duration_seconds")

    def set_parameter(self, name, value):
        """Send one normalized continuous parameter through the active backend."""
        return self.backend.set_parameter(name, value)

    def restrict_scenes_to_backend(self):
        """Apply a hardware-independent QLC+ scene catalog when applicable."""
        self.scene_manager.scenes = {
            name: self.scene_manager.scenes[name]
            for name in self.backend.scene_map.scenes
            if name in self.scene_manager.scenes
        }
        if not self.scene_manager.scenes:
            raise ValueError("The QLC+ scene map contains no scene known to Oculizer")
        current_name = self.scene_manager.current_scene['name']
        if current_name not in self.scene_manager.scenes:
            self.scene_manager.set_scene(next(iter(self.scene_manager.scenes)), apply_fallback=False)

    def reload_scene_configuration(self):
        """Reload scene JSON and the QLC+ logical map without curses coupling."""
        self.scene_manager.reload_scenes()
        self.backend.reload_scene_map()
        self.restrict_scenes_to_backend()

    def stop(self):
        import logging
        logger = logging.getLogger(__name__)
        
        self.running.clear()

        # Only request termination here. Live PortAudio streams are closed by
        # this Oculizer thread's context manager/finally blocks. Closing them
        # from the caller as well creates a native double-close race (observed
        # as free_tiny_botch/SIGABRT with CoreAudio on macOS). File sources use
        # the same lifecycle boundary to wake their pacing wait immediately.
        if self.audio_source is not None:
            self.audio_source.request_stop()
        
        # Stop prediction thread if running
        if self.prediction_thread and self.prediction_thread.is_alive():
            logger.info("Waiting for prediction thread to stop...")
            self.prediction_thread.join(timeout=2.0)
            if self.prediction_thread.is_alive():
                logger.warning("Prediction thread did not stop within timeout")
        
        # The Oculizer runtime thread owns prediction_stream and closes it in
        # its finally block after observing the cleared running event.
        
        if hasattr(self, 'backend'):
            self.backend.close()
