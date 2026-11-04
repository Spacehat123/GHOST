"""Unit tests for Kokoro Speech Engine (CPU/CUDA device management, warm-up, and fail-safe fallback)."""
import os
import unittest
from unittest.mock import patch, MagicMock

from starlette.testclient import TestClient
from ghost.server.app import app
from ghost.voice.kokoro import KokoroSpeechEngine, split_clauses


class TestKokoroVoice(unittest.TestCase):

    def setUp(self):
        # Reset singleton state before each test
        KokoroSpeechEngine._instance = None

    def tearDown(self):
        KokoroSpeechEngine._instance = None

    def test_clause_splitting(self):
        """Split text into conversational clauses with sub-second chunk 1."""
        text = "Understood, boss. Three demos are ready for review. Proceed with deploy?"
        clauses = split_clauses(text)
        self.assertGreaterEqual(len(clauses), 2)
        self.assertIn("Understood, boss.", clauses[0])

    @patch("onnxruntime.InferenceSession")
    @patch("onnxruntime.get_available_providers")
    @patch("kokoro_onnx.Kokoro.from_session")
    def test_kokoro_device_cpu_never_attempts_cuda(self, mock_from_session, mock_avail, mock_session):
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
            call_kwargs = mock_session.call_args[1]
            self.assertEqual(call_kwargs.get("providers"), ["CPUExecutionProvider"])

    @patch("onnxruntime.InferenceSession")
    @patch("onnxruntime.get_available_providers")
    def test_kokoro_device_cuda_raises_if_cuda_fails(self, mock_avail, mock_session):
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

    @patch("onnxruntime.InferenceSession")
    @patch("onnxruntime.get_available_providers")
    @patch("kokoro_onnx.Kokoro.from_session")
    def test_kokoro_device_auto_falls_back_to_cpu(self, mock_from_session, mock_avail, mock_session):
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

    def test_tts_api_response_headers(self):
        """GET /api/tts includes X-Provider, X-Synth-Ms, and X-Clause-Count headers."""
        client = TestClient(app)
        res = client.get("/api/tts?text=Understood%20boss&first_clause=true")
        self.assertEqual(res.status_code, 200)
        self.assertIn("X-Provider", res.headers)
        self.assertIn("X-Synth-Ms", res.headers)
        self.assertIn("X-Clause-Count", res.headers)
        synth_ms = int(res.headers["X-Synth-Ms"])
        self.assertGreaterEqual(synth_ms, 0)


if __name__ == "__main__":
    unittest.main()
