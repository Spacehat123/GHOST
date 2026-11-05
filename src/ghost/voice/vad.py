"""Silero VAD with pre-roll — the ear of the voice stack (CONTEXT.md §2.2, §14.2.6).

Spec from the architecture dossier:
- Silero VAD, 32ms frames (512 samples @ 16kHz)
- sensitivity threshold 0.3-0.4 speech probability
- 150ms pre-roll buffer so the user's first syllable is NEVER clipped
- honest degradation: if the onnx model is unavailable, `available` is False
  and callers fall back to energy-threshold mode — never a fake VAD.

This module is the frame-loop the PLAN described but ghost/voice never had;
the DuplexCoordinator consumes its speech-start/stop callbacks for barge-in.
"""
import collections
import logging
import os
import time
from typing import Callable, Deque, List, Optional

logger = logging.getLogger("ghost.voice.vad")

SAMPLE_RATE = 16000
FRAME_SAMPLES = 512            # 32ms @ 16kHz (CONTEXT.md §2.2)
PRE_ROLL_MS = 150              # §2.2: save 150ms before speech onset
PRE_ROLL_FRAMES = (PRE_ROLL_MS * SAMPLE_RATE // 1000) // FRAME_SAMPLES + 1
SPEECH_THRESHOLD = 0.35        # within the documented 0.3-0.4 band
END_SILENCE_FRAMES = 20        # ~640ms of silence closes an utterance


class SileroVAD:
    """Wraps Silero (onnxruntime). Honest availability; pre-roll guaranteed."""

    def __init__(self, model_path: Optional[str] = None,
                 threshold: float = SPEECH_THRESHOLD):
        self.threshold = threshold
        self.model = None
        self.available = False
        self._load(model_path or os.environ.get("GHOST_SILERO_MODEL_PATH"))

    def _load(self, model_path: Optional[str]) -> None:
        try:
            import onnxruntime
            path = model_path or self._find_model()
            if not path or not os.path.exists(path):
                logger.info("Silero model not present — VAD unavailable "
                            "(energy fallback only).")
                return
            self.model = onnxruntime.InferenceSession(
                path, providers=["CPUExecutionProvider"])
            self.available = True
            logger.info("Silero VAD loaded from %s", path)
        except ImportError:
            logger.info("onnxruntime not installed — Silero VAD unavailable.")
        except Exception as e:
            logger.warning("Silero VAD load failed: %s", e)

    @staticmethod
    def _find_model() -> Optional[str]:
        for candidate in ("silero_vad.onnx", "models/silero_vad.onnx",
                          os.path.expanduser("~/.ghost/models/silero_vad.onnx")):
            if os.path.exists(candidate):
                return candidate
        return None

    def speech_probability(self, frame_samples) -> float:
        """Speech probability for one 512-sample frame. Unavailable -> 0.0
        (callers must treat that as 'no model', not 'silence')."""
        if not self.available:
            return 0.0
        import numpy as np
        audio = np.frombuffer(frame_samples, dtype=np.int16).astype(np.float32) / 32768.0
        if audio.shape[0] != FRAME_SAMPLES:
            return 0.0
        try:
            input_name = self.model.get_inputs()[0].name
            state_name = self.model.get_inputs()[1].name if len(self.model.get_inputs()) > 1 else None
            feeds = {input_name: audio.reshape(1, -1)}
            if state_name:
                feeds[state_name] = self._state
            out = self.model.run(None, feeds)
            if state_name:
                self._state = out[1]
            return float(out[0][0][0] if out[0].ndim > 1 else out[0][0])
        except Exception:
            return 0.0


class PreRollFrameBuffer:
    """Ring buffer keeping the last N frames (150ms) BEFORE speech onset —
    the user's first syllable is never clipped (CONTEXT.md §2.2)."""

    def __init__(self, pre_roll_frames: int = PRE_ROLL_FRAMES):
        self.pre_roll_frames = pre_roll_frames
        self._ring: Deque[bytes] = collections.deque(maxlen=pre_roll_frames)

    def push(self, frame: bytes) -> None:
        self._ring.append(frame)

    def flush_pre_roll(self) -> List[bytes]:
        frames = list(self._ring)
        self._ring.clear()
        return frames


class VADFrameLoop:
    """Consumes PCM frames; emits speech_start (with pre-roll attached),
    speech_end callbacks. Drives the DuplexCoordinator's barge-in."""

    def __init__(self, vad: SileroVAD,
                 on_speech_start: Callable[[List[bytes]], None],
                 on_speech_end: Callable[[bytes], None],
                 energy_fallback_threshold: int = 900):
        self.vad = vad
        self.buffer = PreRollFrameBuffer()
        self.on_speech_start = on_speech_start
        self.on_speech_end = on_speech_end
        self.in_speech = False
        self.silence_run = 0
        self.speech_frames: List[bytes] = []
        self.energy_fallback_threshold = energy_fallback_threshold
        self._state = None

    def _is_speech(self, frame: bytes) -> bool:
        if self.vad.available:
            prob = self.vad.speech_probability(frame)
            return prob >= self.vad.threshold
        # Energy fallback (honest degradation): crude but real.
        # audioop was removed in Python 3.14 — fall through to pure-Python RMS.
        try:
            import audioop
            rms = audioop.rms(frame, 2)
        except ImportError:
            rms = _rms_fallback(frame)
        return rms >= self.energy_fallback_threshold

    def feed(self, frame: bytes) -> None:
        """One 32ms PCM16 frame. Synchronous by design: audio callbacks are
        realtime; anything slow belongs in the consumers."""
        self.buffer.push(frame)
        speech = self._is_speech(frame)
        if speech and not self.in_speech:
            self.in_speech = True
            self.speech_frames = self.buffer.flush_pre_roll()
            self.speech_frames.append(frame)
            self.silence_run = 0
            self.on_speech_start(self.speech_frames)
            return
        if self.in_speech:
            self.speech_frames.append(frame)
            if speech:
                self.silence_run = 0
            else:
                self.silence_run += 1
                if self.silence_run >= END_SILENCE_FRAMES:
                    audio = b"".join(self.speech_frames)
                    self.in_speech = False
                    self.speech_frames = []
                    self.on_speech_end(audio)


def _rms_fallback(frame: bytes) -> float:
    import array
    samples = array.array("h", frame[: len(frame) // 2 * 2])
    if not samples:
        return 0.0
    return (sum(s * s for s in samples) / len(samples)) ** 0.5
