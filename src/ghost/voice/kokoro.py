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
DEFAULT_VOICE = os.environ.get("KOKORO_VOICE", "af_nicole")


def normalize_speech_text(text: str) -> str:
    """
    Normalizes numbers, decimals, versions, and currency into natural spoken text
    so speech engines pronounce them correctly without treating periods as full stops.
    Examples:
      '$23.32' -> '23 dollars and 32 cents'
      '2.2' -> '2 point 2'
      'v1.0' -> 'version 1 point 0'
      '45,000' -> '45000'
    """
    if not text:
        return ""

    # 1. Commas in numbers: 45,000 -> 45000, 1,000,000 -> 1000000
    text = re.sub(r'(?<=\d),(?=\d)', '', text)

    # 2. Versions: v2.2 -> version 2.2
    text = re.sub(r'\bv(\d+\.\d+)\b', r'version \1', text, flags=re.IGNORECASE)

    # 3. Currency formatting
    # Dollars: $23.32 -> 23 dollars and 32 cents
    def replace_dollar(m):
        dollars = int(m.group(1))
        cents_str = m.group(2)
        if cents_str is not None:
            cents = int(cents_str)
            d_unit = "dollar" if dollars == 1 else "dollars"
            c_unit = "cent" if cents == 1 else "cents"
            if dollars == 0 and cents > 0:
                return f"{cents} {c_unit}"
            elif cents == 0:
                return f"{dollars} {d_unit}"
            else:
                return f"{dollars} {d_unit} and {cents} {c_unit}"
        else:
            d_unit = "dollar" if dollars == 1 else "dollars"
            return f"{dollars} {d_unit}"

    text = re.sub(r'\$(\d+)(?:\.(\d{1,2}))?\b', replace_dollar, text)

    # Pounds: £23.50 -> 23 pounds and 50 pence
    def replace_pound(m):
        pounds = int(m.group(1))
        pence_str = m.group(2)
        if pence_str is not None:
            pence = int(pence_str)
            p_unit = "pound" if pounds == 1 else "pounds"
            pen_unit = "penny" if pence == 1 else "pence"
            if pounds == 0 and pence > 0:
                return f"{pence} {pen_unit}"
            elif pence == 0:
                return f"{pounds} {p_unit}"
            else:
                return f"{pounds} {p_unit} and {pence} {pen_unit}"
        else:
            p_unit = "pound" if pounds == 1 else "pounds"
            return f"{pounds} {p_unit}"

    text = re.sub(r'£(\d+)(?:\.(\d{1,2}))?\b', replace_pound, text)

    # Euros: €23.50 -> 23 euros and 50 cents
    def replace_euro(m):
        euros = int(m.group(1))
        cents_str = m.group(2)
        if cents_str is not None:
            cents = int(cents_str)
            e_unit = "euro" if euros == 1 else "euros"
            c_unit = "cent" if cents == 1 else "cents"
            if euros == 0 and cents > 0:
                return f"{cents} {c_unit}"
            elif cents == 0:
                return f"{euros} {e_unit}"
            else:
                return f"{euros} {e_unit} and {cents} {c_unit}"
        else:
            e_unit = "euro" if euros == 1 else "euros"
            return f"{euros} {e_unit}"

    text = re.sub(r'€(\d+)(?:\.(\d{1,2}))?\b', replace_euro, text)

    # Rupees: ₹45000 -> 45000 rupees
    def replace_rupee(m):
        rupees = int(m.group(1))
        paise_str = m.group(2)
        if paise_str is not None:
            paise = int(paise_str)
            if paise == 0:
                return f"{rupees} rupees"
            return f"{rupees} rupees and {paise} paise"
        return f"{rupees} rupees"

    text = re.sub(r'(?:₹|Rs\.?\s*)(\d+)(?:\.(\d{1,2}))?\b', replace_rupee, text)

    # 4. Decimals: 2.2 -> 2 point 2, 3.14 -> 3 point 14
    text = re.sub(r'(\d+)\.(\d+)', r'\1 point \2', text)

    return text


def split_clauses(text: str) -> List[str]:
    """
    Split text into bite-sized conversational clauses for instant streaming playback.
    Ensures chunk 1 synthesizes in <1.2s instead of waiting for an entire paragraph.
    Normalizes numeric expressions (currencies, decimals) so dots are pronounced naturally.
    """
    clean = re.sub(r'\s+', ' ', text).strip()
    if not clean:
        return []
    clean = normalize_speech_text(clean)
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

    def __init__(self, voice: str = DEFAULT_VOICE):
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

        device = (os.environ.get("KOKORO_DEVICE") or KOKORO_DEVICE).lower()
        sess = None

        if device in ("auto", "cuda"):
            # Preload CUDA & cuDNN shared libraries from pip nvidia-* wheels with RTLD_GLOBAL
            # so ONNX Runtime's C++ provider loader resolves libcublasLt, libcudnn, etc.
            try:
                import ctypes
                import glob
                import nvidia
                for n_root in getattr(nvidia, "__path__", []):
                    for lib_dir in sorted(glob.glob(os.path.join(n_root, "*", "lib"))):
                        for so_file in sorted(glob.glob(os.path.join(lib_dir, "*.so*"))):
                            try:
                                ctypes.CDLL(so_file, mode=ctypes.RTLD_GLOBAL)
                            except Exception:
                                pass
            except Exception as e:
                logger.debug(f"NVIDIA wheel preloading skipped: {e}")

            if hasattr(ort, "preload_dlls"):
                try:
                    ort.preload_dlls()  # loads CUDA/cuDNN from pip nvidia-* wheels
                except Exception as e:
                    logger.warning(f"preload_dlls failed: {e}")
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
                lang = "en-gb" if self.voice.startswith("b") else "en-us"
                self._kokoro.create(t, voice=self.voice, speed=1.05, lang=lang)
        except Exception as e:
            logger.warning(f"Kokoro initialization warm-up or setup failed: {e}")

    def is_available(self) -> bool:
        return self._initialized and self._kokoro is not None

    def get_active_provider(self) -> str:
        return self._active_provider

    def get_installed_voices(self) -> List[str]:
        """Return the list of installed voices restricted to English (af, am, bf, bm) and Hindi (hf, hm)."""
        voices = []
        if self._kokoro and hasattr(self._kokoro, "get_voices"):
            try:
                voices = sorted(list(self._kokoro.get_voices()))
            except Exception:
                pass
        if not voices:
            voices = [
                "af_nicole", "af_aoede", "af_bella", "af_heart", "af_jessica", "af_kore", "af_nova", "af_river", "af_sarah", "af_sky", "af_alloy",
                "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam", "am_michael", "am_onyx", "am_puck", "am_santa",
                "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
                "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
                "hf_alpha", "hf_beta", "hm_omega", "hm_psi"
            ]
        allowed_prefixes = ("af_", "am_", "bf_", "bm_", "hf_", "hm_")
        return [v for v in voices if any(v.startswith(p) for p in allowed_prefixes)]

    def synthesize_clause_sync(self, clause: str, voice: Optional[str] = None, speed: float = 1.05) -> Tuple[Optional[bytes], str]:
        """Synchronously synthesize a single clause into WAV bytes."""
        import soundfile as sf
        target_voice = voice or self.voice
        clean_clause = normalize_speech_text(clause.strip())
        lang = "en-gb" if target_voice.startswith("b") else "en-us"
        samples, sr = self._kokoro.create(clean_clause, voice=target_voice, speed=speed, lang=lang)
        buf = io.BytesIO()
        sf.write(buf, samples, sr, format="WAV", subtype="PCM_16")
        return buf.getvalue(), "audio/wav"

    async def synthesize_wav(self, text: str, voice: Optional[str] = None, speed: float = 1.05) -> Tuple[Optional[bytes], str]:
        """
        Synthesize text into WAV audio bytes using Kokoro local engine.
        Returns (audio_bytes, format).
        """
        clean_text = normalize_speech_text(text.strip())
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
