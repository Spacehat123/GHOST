"""Unit and integration tests for Universal Event Bus (INTERFACE_CONTRACTS §1)."""

import asyncio
import os
import tempfile
import time
import unittest

from ghost.core.events import Event, EventBus, EventSeverity


class TestUniversalEvents(unittest.TestCase):

    def test_event_creation_and_serialization(self):
        """Event conforms to schema, computes dedup_id and roundtrips to/from dict."""
        ev = Event(
            event_type="system.telemetry.sample",
            source="collector.system",
            payload={"cpu": 15.4, "ram": 55.0},
            severity=EventSeverity.INFO,
            correlation_id="trace-123",
        )
        self.assertTrue(ev.event_id)
        self.assertEqual(ev.event_type, "system.telemetry.sample")
        self.assertEqual(ev.severity, EventSeverity.INFO)
        self.assertTrue(ev.dedup_id)
        self.assertEqual(ev.correlation_id, "trace-123")

        data = ev.to_dict()
        self.assertEqual(data["event_type"], "system.telemetry.sample")
        self.assertEqual(data["severity"], "INFO")
        self.assertEqual(data["payload"]["cpu"], 15.4)

        restored = Event.from_dict(data)
        self.assertEqual(restored.event_id, ev.event_id)
        self.assertEqual(restored.event_type, ev.event_type)
        self.assertEqual(restored.severity, ev.severity)
        self.assertEqual(restored.payload, ev.payload)

    def test_event_severity_ordering(self):
        """Severity levels correctly map to integer order."""
        self.assertLess(EventSeverity.DEBUG.level_number, EventSeverity.INFO.level_number)
        self.assertLess(EventSeverity.INFO.level_number, EventSeverity.WARNING.level_number)
        self.assertLess(EventSeverity.WARNING.level_number, EventSeverity.ERROR.level_number)
        self.assertLess(EventSeverity.ERROR.level_number, EventSeverity.CRITICAL.level_number)

    def test_pub_sub_exact_and_wildcard(self):
        """Subscribers receive matching events by exact and wildcard pattern."""
        async def _run():
            with tempfile.TemporaryDirectory() as tmpdir:
                db_path = os.path.join(tmpdir, "test_events.db")
                bus = EventBus(db_path=db_path)

                exact_received = []
                system_wildcard_received = []
                all_wildcard_received = []
                dev_received = []

                async def on_exact(ev: Event):
                    exact_received.append(ev)

                def on_system_wildcard(ev: Event):
                    system_wildcard_received.append(ev)

                async def on_all(ev: Event):
                    all_wildcard_received.append(ev)

                def on_dev(ev: Event):
                    dev_received.append(ev)

                bus.subscribe("system.telemetry.sample", on_exact)
                bus.subscribe("system.*", on_system_wildcard)
                bus.subscribe("*", on_all)
                bus.subscribe("dev.*", on_dev)

                ev1 = Event(event_type="system.telemetry.sample", source="test", payload={"val": 1})
                ev2 = Event(event_type="system.resource.alert", source="test", payload={"val": 2})
                ev3 = Event(event_type="dev.git.change", source="test", payload={"val": 3})

                await bus.publish(ev1)
                await bus.publish(ev2)
                await bus.publish(ev3)

                self.assertEqual(len(exact_received), 1)
                self.assertEqual(exact_received[0].payload["val"], 1)

                self.assertEqual(len(system_wildcard_received), 2)
                self.assertEqual(len(all_wildcard_received), 3)
                self.assertEqual(len(dev_received), 1)
                self.assertEqual(dev_received[0].payload["val"], 3)

        asyncio.run(_run())

    def test_deduplication(self):
        """Duplicate events within dedup_window_s are dropped."""
        async def _run():
            with tempfile.TemporaryDirectory() as tmpdir:
                db_path = os.path.join(tmpdir, "test_events.db")
                bus = EventBus(db_path=db_path, dedup_window_s=5.0)

                received = []
                bus.subscribe("*", lambda ev: received.append(ev))

                ev1 = Event(event_type="system.telemetry.sample", source="sensor", payload={"temp": 45})
                ev2 = Event(event_type="system.telemetry.sample", source="sensor", payload={"temp": 45})

                self.assertEqual(ev1.dedup_id, ev2.dedup_id)

                res1 = await bus.publish(ev1)
                res2 = await bus.publish(ev2)

                self.assertTrue(res1)
                self.assertFalse(res2)
                self.assertEqual(len(received), 1)

        asyncio.run(_run())

    def test_subscriber_fault_isolation_and_dead_letter(self):
        """Failing subscriber does not halt other subscribers and records dead letter."""
        async def _run():
            with tempfile.TemporaryDirectory() as tmpdir:
                db_path = os.path.join(tmpdir, "test_events.db")
                bus = EventBus(db_path=db_path)

                healthy_received = []

                def failing_sub(ev: Event):
                    raise RuntimeError("Simulated consumer failure")

                def healthy_sub(ev: Event):
                    healthy_received.append(ev)

                bus.subscribe("system.*", failing_sub)
                bus.subscribe("system.*", healthy_sub)

                ev = Event(event_type="system.telemetry.sample", source="test")
                delivered = await bus.publish(ev)

                self.assertTrue(delivered)
                self.assertEqual(len(healthy_received), 1)

                dead_letters = bus.get_dead_letters()
                self.assertEqual(len(dead_letters), 1)
                self.assertIn("Simulated consumer failure", dead_letters[0]["error"])

        asyncio.run(_run())

    def test_persistence_and_query(self):
        """Events are persisted to SQLite events_v2 and queryable with filters."""
        async def _run():
            with tempfile.TemporaryDirectory() as tmpdir:
                db_path = os.path.join(tmpdir, "test_events.db")
                bus = EventBus(db_path=db_path, enable_persistence=True)

                t0 = time.time()
                ev_info = Event(
                    event_type="dev.git.commit",
                    source="git",
                    severity=EventSeverity.INFO,
                    payload={"hash": "abc1234"},
                )
                ev_warn = Event(
                    event_type="dev.test.failure",
                    source="pytest",
                    severity=EventSeverity.WARNING,
                    payload={"failed": 2},
                )
                ev_crit = Event(
                    event_type="system.resource.alert",
                    source="kernel",
                    severity=EventSeverity.CRITICAL,
                    payload={"reason": "OOM"},
                )

                await bus.publish(ev_info)
                await bus.publish(ev_warn)
                await bus.publish(ev_crit)

                # Query all dev events
                dev_events = bus.query(event_type_prefix="dev.")
                self.assertEqual(len(dev_events), 2)

                # Query only WARNING or higher
                high_sev = bus.query(min_severity=EventSeverity.WARNING)
                self.assertEqual(len(high_sev), 2)
                types = {e.event_type for e in high_sev}
                self.assertEqual(types, {"dev.test.failure", "system.resource.alert"})

        asyncio.run(_run())

    def test_unsubscribe(self):
        """Unsubscribing stops callbacks from firing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_events.db")
            bus = EventBus(db_path=db_path)

            calls = []
            sub_id = bus.subscribe("test.*", lambda ev: calls.append(ev))
            ev = Event(event_type="test.one", source="src")

            bus.publish_sync(ev)
            self.assertEqual(len(calls), 1)

            removed = bus.unsubscribe(sub_id)
            self.assertTrue(removed)

            bus.publish_sync(Event(event_type="test.two", source="src"))
            self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
