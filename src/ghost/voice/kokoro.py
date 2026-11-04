"""Local High-Speed Speech Synthesizer for Ghost using Kokoro-82M INT8 ONNX.
Provides sub-100ms/fast local synthesis with authentic British military tone (bm_george),
clause streaming, and fallback to edge-tts if needed.
"""
import asyncio
import io
import logging
import os
import re
from typing import AsyncGenerator, List, Optional, Tuple

logger = logging.getLogger("ghost.voice.kokoro")

KOKORO_DIR = os.path.expanduser("~/.local/share/cool-shell/models/kokoro")
MODEL_FP32 = os.environ.get("KOKORO_MODEL_FP32", os.path.join(KOKORO_DIR, "kokoro-v1.0.onnx"))
MODEL_INT8 = os.environ.get("KOKORO_MODEL_INT8", os.path.join(KOKORO_DIR, "kokoro-v1.0.int8.onnx"))
KOKORO_VOICES_PATH = os.environ.get("KOKORO_VOICES_PATH", os.path.join(KOKORO_DIR, "voices-v1.0.bin"))
KOKORO_DEVICE = os.environ.get("KOKORO_DEVICE", "auto").lower()

def split_clauses(text: str) -> List[str]:
    """
    Split text into bite-sized conversational clauses for instant streaming playback.
    Ensures chunk 1 synthesizes in <1.2s instead of waiting for an entire paragraph.
    """
    clean = re.sub(r'\s+', ' ', text).strip()
    if not clean:
        return []
    parts = re.split(r'(?<=[.!?:;\n—–])\s+|(?<=,)\s+', clean)
    merged = []
    buf = ''
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if len(buf.split()) < 4:
            buf = (buf + ' ' + p).strip()
        else:
            merged.append(buf)
            buf = p
    if buf:
        merged.append(buf)
    return merged or [clean]


class KokoroSpeechEngine:
    _instance: Optional["KokoroSpeechEngine"] = None
    _kokoro = None
    _initialized: bool = False
    _init_error: Optional[str] = None
    _active_provider: str = "CPUExecutionProvider"

    def __init__(self, voice: str = "bm_george"):
        self.voice = voice

    @classmethod
    def get_instance(cls) -> "KokoroSpeechEngine":
        if cls._instance is None:
            cls._instance = KokoroSpeechEngine()
            cls._instance.initialize()
        return cls._instance

    def initialize(self):
        """Pre-load Kokoro ONNX model and voice weights into memory (GPU or CPU)."""
        if self._initialized:
            return

        try:
            import onnxruntime as ort
            from kokoro_onnx import Kokoro
        except Exception as e:
            self._init_error = f"onnxruntime or kokoro_onnx import failed: {e}"
            logger.error(self._init_error)
            return

        if hasattr(ort, "preload_dlls"):
            try:
                ort.preload_dlls()  # loads CUDA/cuDNN from pip nvidia-* wheels
            except Exception as e:
                logger.warning(f"preload_dlls failed: {e}")

        device = (os.environ.get("KOKORO_DEVICE") or KOKORO_DEVICE).lower()
        sess = None

        if device in ("auto", "cuda"):
            if "CUDAExecutionProvider" in ort.get_available_providers() and os.path.exists(MODEL_FP32):
                so = ort.SessionOptions()
                so.log_severity_level = 2  # show provider-load warnings
                try:
                    sess = ort.InferenceSession(
                        MODEL_FP32,
                        so,
                        providers=[("CUDAExecutionProvider", {
                            "device_id": 0,
                            "cudnn_conv_algo_search": "HEURISTIC",
                            "arena_extend_strategy": "kSameAsRequested",
                            "gpu_mem_limit": 2 * 1024 ** 3,  # 1650 has 4 GB; leave room for display
                        }), "CPUExecutionProvider"],
                    )
                    if sess.get_providers()[0] != "CUDAExecutionProvider":
                        logger.warning(
                            "CUDA requested but session is on CPU; falling back to INT8 CPU path. "
                            "Run scripts/kokoro_gpu_diagnose.py"
                        )
                        sess = None
                except Exception as e:
                    logger.warning(f"CUDA session failed ({e}); falling back to CPU")
                    sess = None

            if sess is None and device == "cuda":
                raise RuntimeError("KOKORO_DEVICE=cuda but the CUDA provider could not start")

        if sess is None:  # CPU path
            so = ort.SessionOptions()
            so.intra_op_num_threads = os.cpu_count() or 4
            so.inter_op_num_threads = 1
            path = MODEL_INT8 if os.path.exists(MODEL_INT8) else MODEL_FP32
            if not os.path.exists(path):
                self._init_error = f"Kokoro model missing at {path}"
                logger.warning(self._init_error)
                return
            try:
                sess = ort.InferenceSession(path, so, providers=["CPUExecutionProvider"])
            except Exception as e:
                self._init_error = f"Failed to initialize CPU inference session: {e}"
                logger.error(self._init_error)
                return

        if not os.path.exists(KOKORO_VOICES_PATH):
            self._init_error = f"Kokoro voices file missing at {KOKORO_VOICES_PATH}"
            logger.warning(self._init_error)
            return

        try:
            self._active_provider = sess.get_providers()[0]
            self._kokoro = Kokoro.from_session(sess, KOKORO_VOICES_PATH)
            self._initialized = True
            logger.warning(f"Kokoro online. Provider={self._active_provider}")

            # warm-up: first call pays kernel/cache setup; do it now, not on user's first sentence
            for t in ("Online.", "Understood, boss. Three demos are ready for review."):
                self._kokoro.create(t, voice=self.voice, speed=1.05, lang="en-gb")
        except Exception as e:
            logger.warning(f"Kokoro initialization warm-up or setup failed: {e}")

    def is_available(self) -> bool:
        return self._initialized and self._kokoro is not None

    def get_active_provider(self) -> str:
        return self._active_provider

    def synthesize_clause_sync(self, clause: str, voice: Optional[str] = None, speed: float = 1.05) -> Tuple[Optional[bytes], str]:
        """Synchronously synthesize a single clause into WAV bytes."""
        import soundfile as sf
        target_voice = voice or self.voice
        lang = "en-gb" if target_voice.startswith("b") else "en-us"
        samples, sr = self._kokoro.create(clause, voice=target_voice, speed=speed, lang=lang)
        buf = io.BytesIO()
        sf.write(buf, samples, sr, format="WAV", subtype="PCM_16")
        return buf.getvalue(), "audio/wav"

    async def synthesize_wav(self, text: str, voice: Optional[str] = None, speed: float = 1.05) -> Tuple[Optional[bytes], str]:
        """
        Synthesize text into WAV audio bytes using Kokoro local engine.
        Returns (audio_bytes, format).
        """
        clean_text = text.strip()
        if not clean_text:
            return None, "empty"

        if not self.is_available():
            return None, f"unavailable: {self._init_error}"

        target_voice = voice or self.voice

        def _run_sync():
            import soundfile as sf
            lang = "en-gb" if target_voice.startswith("b") else "en-us"
            samples, sr = self._kokoro.create(clean_text, voice=target_voice, speed=speed, lang=lang)
            buf = io.BytesIO()
            sf.write(buf, samples, sr, format="WAV", subtype="PCM_16")
            return buf.getvalue()

        try:
            audio_bytes = await asyncio.to_thread(_run_sync)
            return audio_bytes, "audio/wav"
        except Exception as e:
            logger.error(f"Kokoro synthesis failed: {e}")
            return None, str(e)

    async def synthesize_stream_clauses(self, text: str, voice: Optional[str] = None, speed: float = 1.05) -> AsyncGenerator[bytes, None]:
        """
        Synthesize clauses sequentially, yielding each clause's WAV bytes immediately as it finishes.
        Allows instant front-end audio chunk playback for sub-1.5s time-to-first-sound.
        """
        clauses = split_clauses(text)
        if not clauses:
            return

        target_voice = voice or self.voice
        for clause in clauses:
            try:
                wav_bytes, _ = await asyncio.to_thread(self.synthesize_clause_sync, clause, target_voice, speed)
                if wav_bytes:
                    yield wav_bytes
            except Exception as e:
                logger.error(f"Clause synthesis error for '{clause}': {e}")
                continue
