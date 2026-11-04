"""Unit and Integration Tests for Autonomous Watchdog & Cognitive Signal Bus.
Enforces non-negotiable architectural rule:
Persist and deduplicate events without silently discarding important occurrences.
Critical events may trigger background reasoning, but consequential external actions must still require authorization.
"""
import unittest
import tempfile
import shutil
import os
import json
import sqlite3
from unittest.mock import patch, AsyncMock
from ghost.watchdog.service import AutonomousWatchdog, DEFAULT_PATROL_INTERVAL

class TestWatchdog(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.events_db = os.path.join(self.temp_dir, "events.db")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_standardized_patrol_cadence(self):
        """Standardized patrol interval must default to 1800s (30m)."""
        self.assertEqual(DEFAULT_PATROL_INTERVAL, 1800)
        watchdog = AutonomousWatchdog(db_path=self.events_db)
        self.assertEqual(watchdog.patrol_interval, 1800)

    @patch("ghost.mcp.bridge.SystemAppBridge.desktop_notify", new_callable=AsyncMock)
    async def test_lossless_event_persistence_and_deduplication(self, mock_notify):
        """Events are persisted to SQLite; duplicates increment count and append occurrence history without dropping trace data."""
        watchdog = AutonomousWatchdog(db_path=self.events_db)

        # Emit first signal
        evt1 = await watchdog.emit_signal(
            event_type="client_email_received",
            source="email",
            summary="Inbound inquiry from Apex Detailing: 'Website redesign proposal'",
            payload={"client_slug": "apex-detailing", "email": "info@apex.com"},
            urgency="normal"
        )
        self.assertEqual(evt1["occurrence_count"], 1)

        # Emit second identical signal (duplicate)
        evt2 = await watchdog.emit_signal(
            event_type="client_email_received",
            source="email",
            summary="Inbound inquiry from Apex Detailing: 'Website redesign proposal'",
            payload={"client_slug": "apex-detailing", "email": "info@apex.com"},
            urgency="normal"
        )
        self.assertEqual(evt2["occurrence_count"], 2)
        self.assertEqual(evt1["signature"], evt2["signature"])

        # Emit third identical signal
        evt3 = await watchdog.emit_signal(
            event_type="client_email_received",
            source="email",
            summary="Inbound inquiry from Apex Detailing: 'Website redesign proposal'",
            payload={"client_slug": "apex-detailing", "email": "info@apex.com"},
            urgency="normal"
        )
        self.assertEqual(evt3["occurrence_count"], 3)

        # Query database directly to verify lossless persistence
        conn = sqlite3.connect(self.events_db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM system_events").fetchall()

        # Must have exactly 1 record with occurrence_count == 3
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["occurrence_count"], 3)
        self.assertEqual(row["event_type"], "client_email_received")

        # Full occurrence history must be preserved
        occurrences = json.loads(row["occurrences_json"])
        self.assertEqual(len(occurrences), 3)
        for occ in occurrences:
            self.assertIn("timestamp", occ)
            self.assertIn("Apex Detailing", occ["summary"])

        conn.close()

    @patch("ghost.mcp.bridge.SystemAppBridge.desktop_notify", new_callable=AsyncMock)
    async def test_critical_event_queued_unprocessed(self, mock_notify):
        """Critical events are persisted with status 'unprocessed' for cognitive evaluation."""
        watchdog = AutonomousWatchdog(db_path=self.events_db)

        await watchdog.emit_signal(
            event_type="vercel_token_expired",
            source="vercel_patrol",
            summary="CRITICAL: Vercel PAT expired. Immediate renewal required.",
            payload={"status": "expired", "code": 401},
            urgency="critical"
        )

        unprocessed = watchdog.get_unprocessed_events(limit=5)
        self.assertEqual(len(unprocessed), 1)
        self.assertEqual(unprocessed[0]["urgency"], "critical")
        self.assertEqual(unprocessed[0]["event_type"], "vercel_token_expired")

        # Mark processed
        watchdog.mark_event_processed(unprocessed[0]["id"])
        unprocessed_after = watchdog.get_unprocessed_events(limit=5)
        self.assertEqual(len(unprocessed_after), 0)

if __name__ == "__main__":
    unittest.main()
