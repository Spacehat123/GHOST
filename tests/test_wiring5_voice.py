"""Wiring-5 tests: narrator→GUI broadcast, orb WORKING palette, VAD pre-roll."""
import ast
import json
import os
import struct
import tempfile
import unittest
import uuid

TMP = tempfile.mkdtemp(prefix="ghost-w5-test-")
os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(TMP, f"audit-{uuid.uuid4().hex}.db")

from ghost.core.audit import AuditLedger
from ghost.voice.narrator import Narrator, set_speech_broadcast
from ghost.voice.vad import (
    PreRollFrameBuffer, SileroVAD, VADFrameLoop,
    FRAME_SAMPLES, PRE_ROLL_MS,
)


class TestNarratorBroadcast(unittest.TestCase):

    def test_spoken_lines_reach_gui_broadcast(self):
        received = []
        set_speech_broadcast(lambda et, p: received.append((et, p)))
        self.addCleanup(set_speech_broadcast, None)
        n = Narrator(speak=lambda t: None, now=lambda: 1000.0)
        n.on_event("lead_hunt.candidates", {"count": 6, "task_class": "long"}, subject="t")
        self.assertEqual(len(received), 1)
        et, payload = received[0]
        self.assertEqual(et, "narrator.speak")
        self.assertEqual(payload["utterance"], "Found 6 businesses without websites.")
        self.assertEqual(payload["task"], "t")

    def test_server_startup_wires_broadcast(self):
        src = open(os.path.join(os.path.dirname(__file__), "..",
                                "src/ghost/server/app.py")).read()
        self.assertIn("set_speech_broadcast", src)
        self.assertIn("broadcast_agency_event", src)


class TestOrbWorkingPulse(unittest.TestCase):

    def _orb_js(self):
        return open(os.path.join(os.path.dirname(__file__), "..",
                                 "gui/static/js/orb.js")).read()

    def test_working_palette_exists(self):
        src = self._orb_js()
        self.assertIn("WORKING: {", src)
        # Subtle pulse: slower than IDLE (§14.2.5 "visible state without words")
        import re
        m = re.search(r"WORKING: \{.*?speedMult: ([0-9.]+)", src, re.S)
        self.assertIsNotNone(m)
        self.assertLess(float(m.group(1)), 1.0)

    def test_app_maps_events_to_states(self):
        src = open(os.path.join(os.path.dirname(__file__), "..",
                                "gui/static/js/app.js")).read()
        self.assertIn("narrator.speak", src)
        self.assertIn("orb.setState('WORKING')", src)
        self.assertIn("orb.setState('IDLE')", src)


class TestVADPreRoll(unittest.TestCase):

    def _frame(self, amp: int) -> bytes:
        return struct.pack("<512h", *([amp] * FRAME_SAMPLES))

    def test_pre_roll_window_is_150ms_class(self):
        buf = PreRollFrameBuffer()
        self.assertGreaterEqual(buf.pre_roll_frames * 32, 150 - 32)  # >= ~150ms

    def test_silero_unavailable_is_honest(self):
        vad = SileroVAD(model_path="/nonexistent/path.onnx")
        self.assertFalse(vad.available)
        self.assertEqual(vad.speech_probability(self._frame(1000)), 0.0)

    def test_energy_fallback_detects_loud_frames(self):
        vad = SileroVAD(model_path="/nonexistent/path.onnx")
        got_start, got_end = [], []
        loop = VADFrameLoop(vad, on_speech_start=got_start.append,
                            on_speech_end=got_end.append)
        loud, quiet = self._frame(4000), self._frame(50)
        for _ in range(3):
            loop.feed(quiet)          # silence fills pre-roll
        for _ in range(5):
            loop.feed(loud)           # speech starts (pre-roll attached)
        self.assertEqual(len(got_start), 1)
        self.assertGreaterEqual(len(got_start[0]), 6)  # pre-roll + speech frames
        for _ in range(25):
            loop.feed(quiet)          # > END_SILENCE_FRAMES closes utterance
        self.assertEqual(len(got_end), 1)
        audio = got_end[0]
        self.assertGreater(len(audio), 0)
        # first syllable preserved: loud samples exist in the FIRST pre-roll frame
        first_frame = audio[: FRAME_SAMPLES * 2]
        peak = max(abs(s) for (s,) in struct.iter_unpack("<h", first_frame))
        self.assertEqual(peak, 50)  # quiet pre-roll, not clipped speech-only

    def test_frame_size_is_32ms_spec(self):
        self.assertEqual(FRAME_SAMPLES, 512)   # CONTEXT.md §2.2
        self.assertAlmostEqual(FRAME_SAMPLES / 16000 * 1000, 32.0)


if __name__ == "__main__":
    unittest.main()
