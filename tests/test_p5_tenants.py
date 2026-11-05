"""P5 scaffolding tests: tenant registry fail-closed config, Decimal revenue
split, agency lead queue over the durable job queue."""
import json
import os
import tempfile
import unittest
import uuid
from decimal import Decimal

TMP = tempfile.mkdtemp(prefix="ghost-p5-test-")
os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(TMP, f"audit-{uuid.uuid4().hex}.db")
os.environ["GHOST_QUEUE_DB_PATH"] = os.path.join(TMP, f"queue-{uuid.uuid4().hex}.db")

from ghost.core.audit import AuditLedger
from ghost.core.jobqueue import JobQueue
from ghost.tenants.agency import (
    AgencyLeadQueue, TenantRegistry, revenue_split,
)


def write_tenants(cfg):
    path = os.path.join(TMP, f"tenants-{uuid.uuid4().hex}.json")
    with open(path, "w") as f:
        json.dump({"tenants": cfg}, f)
    return path


GOOD = {
    "agc1": {
        "name": "Studio Nova", "from_name": "Studio Nova Web",
        "reply_to": "ops@studionova.dev", "sending_domain": "mail.studionova.dev",
        "revenue_split_pct": 60, "active": True,
    }
}


class TestTenantRegistry(unittest.TestCase):

    def test_loads_valid_tenant(self):
        reg = TenantRegistry(write_tenants(json.loads(json.dumps(GOOD))))
        t = reg.get("agc1")
        self.assertIsNotNone(t)
        self.assertEqual(t.revenue_split_pct, 60.0)

    def test_bad_split_refused_not_coerced(self):
        bad = dict(GOOD)
        bad["bad1"] = {"name": "X", "reply_to": "a@b.c",
                       "sending_domain": "m.x.dev", "revenue_split_pct": 150}
        reg = TenantRegistry(write_tenants(bad))
        self.assertIsNone(reg.get("bad1"))          # refused
        self.assertIsNotNone(reg.get("agc1"))       # good one still loads

    def test_missing_reply_to_refused(self):
        bad = {"t1": {"name": "X", "sending_domain": "m.x.dev", "revenue_split_pct": 50}}
        reg = TenantRegistry(write_tenants(bad))
        self.assertIsNone(reg.get("t1"))

    def test_inactive_tenant_invisible(self):
        cfg = dict(GOOD)
        cfg = json.loads(json.dumps(GOOD))
        cfg["agc1"]["active"] = False
        reg = TenantRegistry(write_tenants(cfg))
        self.assertIsNone(reg.get("agc1"))


class TestRevenueSplit(unittest.TestCase):

    def test_exact_split(self):
        r = revenue_split(20000, 60)
        self.assertEqual(r["agency_share_inr"], Decimal("12000.00"))
        self.assertEqual(r["platform_share_inr"], Decimal("8000.00"))
        self.assertEqual(r["agency_share_inr"] + r["platform_share_inr"], Decimal("20000.00"))

    def test_rounding_preserves_total(self):
        r = revenue_split("100.01", 33.33)
        total = r["agency_share_inr"] + r["platform_share_inr"]
        self.assertEqual(total, Decimal("100.01"))

    def test_junk_money_raises(self):
        with self.assertRaises(ValueError):
            revenue_split("lots", 50)
        with self.assertRaises(ValueError):
            revenue_split(-5, 50)
        with self.assertRaises(ValueError):
            revenue_split(100, 100)  # 100% split = platform gets nothing: refuse


class TestAgencyLeadQueue(unittest.TestCase):

    def setUp(self):
        self.q = JobQueue(db_path=os.path.join(TMP, f"q-{uuid.uuid4().hex}.db"))
        self.reg = TenantRegistry(write_tenants(json.loads(json.dumps(GOOD))))
        self.aq = AgencyLeadQueue(self.q, self.reg)

    def test_submit_enqueues_idempotent_job(self):
        r = self.aq.submit_lead("agc1", {"business_name": "Salt",
                                         "contact_email": "o@salt.in"})
        self.assertTrue(r["ok"])
        r2 = self.aq.submit_lead("agc1", {"business_name": "Salt",
                                          "contact_email": "o@salt.in"})
        # distinct refs — each submission is a real lead, but each is one job
        self.assertNotEqual(r["ref"], r2["ref"])
        self.assertEqual(self.q.stats()["pending"], 2)

    def test_submit_requires_known_tenant(self):
        r = self.aq.submit_lead("ghost-tenant", {"business_name": "X",
                                                 "contact_email": "a@b.c"})
        self.assertFalse(r["ok"])

    def test_submit_requires_minimum_lead_data(self):
        r = self.aq.submit_lead("agc1", {"business_name": "Salt"})
        self.assertFalse(r["ok"])
        self.assertIn("contact_email", r["error"])

    def test_agency_view_is_sanitized(self):
        rows = [{"ref": "agc1-1", "tenant_id": "agc1", "status": "demo_ready",
                 "demo_url": "https://x", "internal_cost_inr": 33},
                {"ref": "other-1", "tenant_id": "other", "status": "delivered"}]
        view = self.aq.agency_view("agc1", rows)
        self.assertEqual(len(view), 1)
        self.assertNotIn("internal_cost_inr", view[0])


if __name__ == "__main__":
    unittest.main()
