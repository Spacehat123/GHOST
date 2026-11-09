"""Unit tests for Kokoro Speech Engine (CPU/CUDA device management, warm-up, and fail-safe fallback)."""
import importlib
import os
import unittest
from unittest.mock import patch, MagicMock

from starlette.testclient import TestClient
from ghost.server.app import app
from ghost.voice.kokoro import KokoroSpeechEngine, split_clauses, normalize_speech_text


class TestKokoroVoice(unittest.TestCase):

    def setUp(self):
        # Reset singleton state before each test
        KokoroSpeechEngine._instance = None

    def tearDown(self):
        KokoroSpeechEngine._instance = None

    def test_default_voice_is_af_nicole(self):
        """Default Kokoro voice persona is af_nicole."""
        engine = KokoroSpeechEngine()
        self.assertEqual(engine.voice, "af_nicole")

    def test_number_and_currency_normalization(self):
        """Dots in decimals (2.2) and currencies ($23.32) are normalized to spoken text."""
        self.assertEqual(
            normalize_speech_text("I made $23.32 tonight, sir."),
            "I made 23 dollars and 32 cents tonight, sir."
        )
        self.assertEqual(
            normalize_speech_text("Version 2.2 is out."),
            "Version 2 point 2 is out."
        )
        self.assertEqual(
            normalize_speech_text("It costs $1.00."),
            "It costs 1 dollar."
        )
        self.assertEqual(
            normalize_speech_text("It costs $0.50."),
            "It costs 50 cents."
        )
        self.assertEqual(
            normalize_speech_text("Revenue was ₹45,000."),
            "Revenue was 45000 rupees."
        )

    def test_import_ghost_server_app(self):
        """Ensure ghost.server.app imports cleanly without NameError or missing dependency errors."""
        mod = importlib.import_module("ghost.server.app")
        self.assertIsNotNone(mod.app)

    def test_clause_splitting(self):
        """Split text into conversational clauses with sub-second chunk 1."""
        text = "Understood, boss. Three demos are ready for review. Proceed with deploy?"
        clauses = split_clauses(text)
        self.assertGreaterEqual(len(clauses), 2)
        self.assertIn("Understood, boss.", clauses[0])

    @patch("os.path.exists", return_value=True)
    @patch("onnxruntime.InferenceSession")
    @patch("onnxruntime.get_available_providers")
    @patch("kokoro_onnx.Kokoro.from_session")
    def test_kokoro_device_cpu_never_attempts_cuda(self, mock_from_session, mock_avail, mock_session, mock_exists):
        """When KOKORO_DEVICE=cpu, CUDA is never attempted even if available."""
        mock_avail.return_value = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        mock_sess_instance = MagicMock()
        mock_sess_instance.get_providers.return_value = ["CPUExecutionProvider"]
        mock_session.return_value = mock_sess_instance
        mock_from_session.return_value = MagicMock()

        with patch.dict(os.environ, {"KOKORO_DEVICE": "cpu"}):
            import ghost.voice.kokoro as k_mod
            k_mod.KOKORO_DEVICE = "cpu"
            engine = KokoroSpeechEngine()
            engine.initialize()

            self.assertEqual(engine.get_active_provider(), "CPUExecutionProvider")
            # Verify InferenceSession was called with only CPUExecutionProvider
            self.assertTrue(mock_session.called)
            call_kwargs = mock_session.call_args[1]
            self.assertEqual(call_kwargs.get("providers"), ["CPUExecutionProvider"])
            # Verify get_available_providers was never called to check CUDA
            mock_avail.assert_not_called()

    @patch("os.path.exists", return_value=True)
    @patch("onnxruntime.InferenceSession")
    @patch("onnxruntime.get_available_providers")
    def test_kokoro_device_cuda_raises_if_cuda_fails(self, mock_avail, mock_session, mock_exists):
        """When KOKORO_DEVICE=cuda and CUDA provider fails or drops to CPU, RuntimeError is raised."""
        mock_avail.return_value = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        # Mock CUDA session falling back to CPUExecutionProvider
        mock_sess_instance = MagicMock()
        mock_sess_instance.get_providers.return_value = ["CPUExecutionProvider"]
        mock_session.return_value = mock_sess_instance

        with patch.dict(os.environ, {"KOKORO_DEVICE": "cuda"}):
            import ghost.voice.kokoro as k_mod
            k_mod.KOKORO_DEVICE = "cuda"
            engine = KokoroSpeechEngine()
            with self.assertRaises(RuntimeError) as ctx:
                engine.initialize()
            self.assertIn("KOKORO_DEVICE=cuda but the CUDA provider could not start", str(ctx.exception))

        # Also verify when CUDAExecutionProvider is completely missing from available providers
        KokoroSpeechEngine._instance = None
        mock_avail.return_value = ["CPUExecutionProvider"]
        with patch.dict(os.environ, {"KOKORO_DEVICE": "cuda"}):
            import ghost.voice.kokoro as k_mod
            k_mod.KOKORO_DEVICE = "cuda"
            engine = KokoroSpeechEngine()
            with self.assertRaises(RuntimeError) as ctx:
                engine.initialize()
            self.assertIn("KOKORO_DEVICE=cuda but the CUDA provider could not start", str(ctx.exception))

    @patch("os.path.exists", return_value=True)
    @patch("onnxruntime.InferenceSession")
    @patch("onnxruntime.get_available_providers")
    @patch("kokoro_onnx.Kokoro.from_session")
    def test_kokoro_device_auto_falls_back_to_cpu(self, mock_from_session, mock_avail, mock_session, mock_exists):
        """When KOKORO_DEVICE=auto and CUDA provider fails, it seamlessly falls back to CPU INT8."""
        mock_avail.return_value = ["CUDAExecutionProvider", "CPUExecutionProvider"]

        # First call (CUDA attempt) raises exception; second call (CPU fallback) succeeds
        cpu_sess = MagicMock()
        cpu_sess.get_providers.return_value = ["CPUExecutionProvider"]
        mock_session.side_effect = [Exception("CUDA driver library missing"), cpu_sess]
        mock_from_session.return_value = MagicMock()

        with patch.dict(os.environ, {"KOKORO_DEVICE": "auto"}):
            import ghost.voice.kokoro as k_mod
            k_mod.KOKORO_DEVICE = "auto"
            engine = KokoroSpeechEngine()
            engine.initialize()

            self.assertTrue(engine.is_available())
            self.assertEqual(engine.get_active_provider(), "CPUExecutionProvider")

    @patch("edge_tts.Communicate")
    def test_tts_api_response_headers(self, mock_edge):
        """GET /api/tts includes X-Provider, X-Synth-Ms, and X-Clause-Count headers without GPU/models/internet."""
        # Mock Edge-TTS stream fallback
        async def mock_stream(*args, **kwargs):
            yield {"type": "audio", "data": b"RIFF_MOCK_AUDIO_PAYLOAD"}

        mock_instance = MagicMock()
        mock_instance.stream = mock_stream
        mock_edge.return_value = mock_instance

        # Test with Kokoro unavailable (fallback to EdgeTTS)
        with patch.object(KokoroSpeechEngine, "is_available", return_value=False):
            client = TestClient(app)
            res = client.get("/api/tts?text=Understood%20boss&first_clause=true")
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.headers["X-Provider"], "EdgeTTS")
            self.assertIn("X-Synth-Ms", res.headers)
            self.assertEqual(res.headers["X-Clause-Count"], "1")
            self.assertEqual(res.content, b"RIFF_MOCK_AUDIO_PAYLOAD")

        # Test with Kokoro available (primary path)
        mock_engine = MagicMock()
        mock_engine.is_available.return_value = True
        mock_engine.get_active_provider.return_value = "CPUExecutionProvider"
        async def mock_synth(text, voice="bm_george", speed=1.05):
            return b"WAV_MOCK_BYTES", "audio/wav"
        mock_engine.synthesize_wav = mock_synth

        with patch.object(KokoroSpeechEngine, "get_instance", return_value=mock_engine):
            client = TestClient(app)
            res = client.get("/api/tts?text=Understood%20boss&first_clause=true")
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.headers["X-Provider"], "CPUExecutionProvider")
            self.assertIn("X-Synth-Ms", res.headers)
            self.assertEqual(res.headers["X-Clause-Count"], "1")
            self.assertEqual(res.content, b"WAV_MOCK_BYTES")


if __name__ == "__main__":
    unittest.main()
