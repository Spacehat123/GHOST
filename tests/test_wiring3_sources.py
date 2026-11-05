"""Wiring-3 tests: lead sources (honest not_configured), extended monitors,
subscription webhooks, Route payouts."""
import json
import os
import tempfile
import unittest
import uuid

TMP = tempfile.mkdtemp(prefix="ghost-w3-test-")
os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(TMP, f"audit-{uuid.uuid4().hex}.db")
os.environ["GHOST_RETAINER_DB_PATH"] = os.path.join(TMP, f"ret-{uuid.uuid4().hex}.db")

from ghost.core.audit import AuditLedger
from ghost.workers.lead_sources import (
    google_places, dead_link_directories, instagram_business, mca_new_filings,
    run_all_sources,
)
from ghost.workers.site_monitor import (
    check_domain_expiry, check_analytics_heartbeat, check_pagespeed,
    check_uptime_multi_region,
)
from ghost.workers.retainers import (
    RetainerStore, process_subscription_webhook, route_transfer,
)
from ghost.workers.payments import PaymentEvent
from decimal import Decimal


class TestLeadSources(unittest.TestCase):

    def test_places_without_key_is_honest(self):
        os.environ.pop("GOOGLE_PLACES_API_KEY", None)
        r = google_places("fine dining", "Bengaluru")
        self.assertEqual(r["status"], "not_configured")
        self.assertEqual(r["candidates"], [])
        self.assertIn("GOOGLE_PLACES_API_KEY", r["setup"])

    def test_places_filters_no_website(self):
        os.environ["GOOGLE_PLACES_API_KEY"] = "test-key"
        self.addCleanup(os.environ.pop, "GOOGLE_PLACES_API_KEY", None)
        payload = {"places": [
            {"displayName": {"text": "Has Site"}, "websiteUri": "https://real.com",
             "formattedAddress": "a", "primaryType": "restaurant"},
            {"displayName": {"text": "No Site"}, "formattedAddress": "b",
             "primaryType": "restaurant", "nationalPhoneNumber": "+91"},
            {"displayName": {"text": "Fb Only"}, "websiteUri": "https://facebook.com/x",
             "formattedAddress": "c", "primaryType": "restaurant"},
        ]}
        import ghost.workers.lead_sources as ls
        orig = ls.urllib.request.urlopen
        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self): return json.dumps(payload).encode()
        ls.urllib.request.urlopen = lambda req, timeout=None: FakeResp()
        try:
            r = ls.google_places("fine dining", "Bengaluru")
        finally:
            ls.urllib.request.urlopen = orig
        names = [c["business_name"] for c in r["candidates"]]
        self.assertEqual(names, ["No Site", "Fb Only"])

    def test_dead_link_directory_finds_broken_sites(self):
        html = "<a href='https://deadshop.com/store'>Shop</a> <a href='https://alive.com/x'>X</a>"
        def fetch(url, timeout=10):
            if url.startswith("https://dir.example"):
                return 200, html
            if "deadshop" in url:
                raise OSError("DNS resolution failed")
            return 200, "ok"
        r = dead_link_directories(["https://dir.example/list"], fetch=fetch)
        self.assertEqual(len(r["candidates"]), 1)
        self.assertEqual(r["candidates"][0]["evidence"]["pitch"], "broken_website")

    def test_instagram_and_mca_not_configured(self):
        os.environ.pop("INSTAGRAM_ACCESS_TOKEN", None)
        os.environ.pop("MCA_DATA_FEED_URL", None)
        self.assertEqual(instagram_business("Bengaluru")["status"], "not_configured")
        self.assertEqual(mca_new_filings()["status"], "not_configured")

    def test_run_all_aggregates_honestly(self):
        r = run_all_sources("Bengaluru", directory_urls=[])
        self.assertEqual(set(r["sources"]), {"google_places", "dead_link_dirs",
                                             "instagram", "mca_filics".replace("lics", "lings")})
        self.assertIn(r["sources"]["google_places"]["status"], ("ok", "not_configured", "error"))


class TestExtendedMonitors(unittest.TestCase):

    def test_domain_expiry_via_rdap(self):
        rdap = json.dumps({"events": [
            {"eventAction": "registration", "eventDate": "2020-01-01T00:00:00Z"},
            {"eventAction": "expiration", "eventDate": "2027-01-01T00:00:00Z"}]})
        def fetch(url, timeout=8):
            return 200, rdap
        r = check_domain_expiry("https://acme.com", fetch=fetch)
        self.assertTrue(r["ok"])
        self.assertGreater(r["evidence"]["days_left"], 30)

    def test_domain_expiring_soon_fails(self):
        import datetime
        soon = (datetime.datetime.utcnow() + datetime.timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        def fetch(url, timeout=8):
            return 200, json.dumps({"events": [{"eventAction": "expiration",
                                                "eventDate": soon}]})
        r = check_domain_expiry("https://acme.com", fetch=fetch)
        self.assertFalse(r["ok"])

    def test_analytics_heartbeat(self):
        self.assertTrue(check_analytics_heartbeat(
            "https://x", fetch=lambda u, timeout=8: (200, "<script>gtag('js')</script>"))["ok"])
        self.assertFalse(check_analytics_heartbeat(
            "https://x", fetch=lambda u, timeout=8: (200, "<html>nothing</html>"))["ok"])

    def test_pagespeed_without_key_honest(self):
        os.environ.pop("PAGESPEED_API_KEY", None)
        r = check_pagespeed("https://x.com")
        self.assertTrue(r["ok"])  # not a client incident
        self.assertTrue(r["evidence"].get("not_configured"))

    def test_multi_region_quorum(self):
        probes = {
            "in": lambda u, timeout=8: (200, "ok"),
            "eu": lambda u, timeout=8: (200, "ok"),
            "us": lambda u, timeout=8: (_ for _ in ()).throw(OSError("timeout")),
        }
        r = check_uptime_multi_region("https://x", region_probes=probes)
        self.assertFalse(r["ok"])
        self.assertEqual(r["evidence"]["verdict"], "degraded")
        probes_bad = {k: (lambda u, timeout=8: (_ for _ in ()).throw(OSError("t")))
                      for k in ("a", "b")}
        r2 = check_uptime_multi_region("https://x", region_probes=probes_bad)
        self.assertEqual(r2["evidence"]["verdict"], "down")


class TestSubscriptionsAndRoute(unittest.TestCase):

    def setUp(self):
        self.store = RetainerStore(db_path=os.path.join(TMP, f"r-{uuid.uuid4().hex}.db"))
        self.store.register_site("acme", "https://acme.com", amount_inr=8000)

    def test_subscription_charged_resets_dunning(self):
        for _ in range(2):
            self.store.record_charge_failed("acme")
        self.assertEqual(self.store.get_site("acme")["billing_state"], "dunning")
        provider = FakeSignedProvider(event_type="subscription.charged")
        r = process_subscription_webhook(provider, {}, b"body", self.store)
        self.assertEqual(r["action"], "billed")
        self.assertEqual(self.store.get_site("acme")["billing_state"], "active")

    def test_subscription_halted_enters_dunning(self):
        provider = FakeSignedProvider(event_type="subscription.halted")
        r = process_subscription_webhook(provider, {}, b"body", self.store)
        self.assertEqual(r["action"], "dunning")
        self.assertEqual(self.store.get_site("acme")["billing_state"], "dunning")

    def test_unsigned_subscription_refused(self):
        provider = FakeSignedProvider(verify=False)
        r = process_subscription_webhook(provider, {}, b"body", self.store)
        self.assertEqual(r["status"], "rejected")

    def test_route_without_keys_refuses(self):
        os.environ.pop("RAZORPAY_ROUTE_KEY", None)
        r = route_transfer(None, 20000, 60, "fund_acct_x")
        self.assertEqual(r["status"], "not_configured")

    def test_route_split_math_before_network(self):
        # even in not_configured state, the split is computed Decimal-exactly
        from ghost.tenants.agency import revenue_split
        s = revenue_split(20000, 60)
        self.assertEqual(s["agency_share_inr"] + s["platform_share_inr"], 20000)


class FakeSignedProvider:
    name = "razorpay"
    def __init__(self, verify=True, event_type="payment.captured"):
        self.verify_ok = verify
        self.event_type = event_type
    def verify_webhook(self, headers, body):
        return self.verify_ok
    def parse_event(self, body):
        return PaymentEvent(f"evt-{uuid.uuid4().hex[:8]}", self.event_type,
                            Decimal(8000), "INR", "INV-1", "acme", "captured", {})


if __name__ == "__main__":
    unittest.main()
