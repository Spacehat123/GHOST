"""Unit tests for Batch Demo Factory Worker (Part 3 & Acceptance)."""
import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import patch, AsyncMock

from ghost.crm.db import CRMDatabase
from ghost.workers.batch_demo_worker import (
    BatchDemoWorker,
    run_quality_gates,
    load_factory_config
)
from ghost.workers.demo_state_machine import AutonomousDemoStateMachine

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def mock_screenshot_generator(out_dir: str):
    """Generates mock non-empty screenshots for testing."""
    for name in ["desktop_1440x900.jpg", "mobile_390x844.jpg"]:
        p = os.path.join(out_dir, name)
        with open(p, "wb") as f:
            f.write(b"\xFF\xD8\xFF\xE0" + b"\x00" * 2000)


class TestBatchDemoWorker(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.demos_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test_crm.db")
        self.crm = CRMDatabase(db_path=self.db_path)
        self.worker = BatchDemoWorker(crm=self.crm)

        # Patch DEMOS_DIR so tests don't pollute local workspace
        self.demos_patcher = patch("ghost.workers.batch_demo_worker.DEMOS_DIR", self.demos_dir.name)
        self.sm_demos_patcher = patch("ghost.workers.demo_state_machine.DEMOS_DIR", self.demos_dir.name)
        self.demos_patcher.start()
        self.sm_demos_patcher.start()

        # Create a test campaign and batch
        self.campaign = self.crm.create_campaign("London Factory Test", "London")
        self.batch = self.crm.create_batch(self.campaign["id"], "car detailing", batch_id="batch_factory_1")

    def tearDown(self):
        self.demos_patcher.stop()
        self.sm_demos_patcher.stop()
        self.demos_dir.cleanup()
        self.tmpdir.cleanup()

    def test_golden_fixtures_build_and_pass_quality_gates(self):
        """All 5 per-category golden fixtures (detailing, dental, gym, restaurant, salon) build cleanly and pass quality gates."""
        fixtures = [
            ("detailing.json", "home_trade_services"),
            ("dental.json", "dental_medical"),
            ("gym.json", "gyms_fitness"),
            ("restaurant.json", "restaurants_cafes"),
            ("salon.json", "salons_spas")
        ]

        for fname, expected_domain in fixtures:
            fpath = os.path.join(FIXTURES_DIR, fname)
            with open(fpath, "r", encoding="utf-8") as f:
                scout_data = json.load(f)

            b_name = scout_data["business_name"]
            slug = AutonomousDemoStateMachine.slugify(b_name)
            sm = AutonomousDemoStateMachine(
                business_name=b_name,
                location=scout_data.get("address"),
                slug=slug
            )

            with patch("ghost.workers.recon_scout.ReconScout.scout_business", new_callable=AsyncMock) as m_scout, \
                 patch("ghost.workers.audit_engine.PerformanceAuditor.audit_url", new_callable=AsyncMock) as m_audit, \
                 patch("ghost.mcp.bridge.WebSearchBridge.search", new_callable=AsyncMock) as m_search:
                m_scout.return_value = scout_data
                m_audit.return_value = None
                m_search.return_value = {"results": []}

                asyncio.run(sm.run_phase1_recon())
                self.assertEqual(sm.domain, expected_domain, f"Fixture {fname} domain mismatch")
                self.assertFalse(sm.needs_confirmation)

                asyncio.run(sm.run_phase2_design_brief())
                asyncio.run(sm.run_phase3_build_sections())
                mock_screenshot_generator(sm.out_dir)

            passed, err = run_quality_gates(slug, sm.domain, sm.out_dir)
            self.assertTrue(passed, f"Quality gate failed for fixture {fname}: {err}")

    def test_mock_batch_of_33_leads_execution_and_resilience(self):
        """
        Acceptance test:
        Batch of 33 leads:
        - 28 clean leads reach 'built'
        - 4 ambiguous / domain mismatch leads stop in 'needs_confirmation' (with CONFIRM.json)
        - 1 lead throws an error during build
        Result: Thrown exception on 1 lead does not stop the batch; clean leads reach 'built'.
        """
        # Create 33 qualified leads in the batch
        # 1-28: Clean detailing shops
        for i in range(1, 29):
            self.crm.add_or_update_lead({
                "batch_id": self.batch["id"],
                "slug": f"clean-detailer-{i}",
                "name": f"Clean Detailer {i}",
                "address": f"{i} Park Lane, London W1K",
                "phone": f"+44 20 7946 {i:04d}",
                "website": f"https://detailer{i}.co.uk",
                "rating": 4.8,
                "review_count": 35,
                "google_category": "Car detailing service",
                "status": "qualified",
                "score_total": 75.0
            })

        # 29-30: Ambiguous names with no category -> needs_confirmation (low confidence)
        self.crm.add_or_update_lead({
            "batch_id": self.batch["id"],
            "slug": "ambiguous-enterprise-29",
            "name": "Quantum Flux Syndicate",
            "address": "29 Fleet St, London",
            "phone": "+44 20 7946 0029",
            "website": None,
            "rating": 4.5,
            "review_count": 20,
            "google_category": None,
            "status": "qualified",
            "score_total": 60.0
        })
        self.crm.add_or_update_lead({
            "batch_id": self.batch["id"],
            "slug": "ambiguous-enterprise-30",
            "name": "Vortex Horizon Group",
            "address": "30 Fleet St, London",
            "phone": "+44 20 7946 0030",
            "website": None,
            "rating": 4.5,
            "review_count": 20,
            "google_category": None,
            "status": "qualified",
            "score_total": 60.0
        })

        # 31-32: Domain mismatch -> categorised as restaurant/bakery in a car detailing batch
        self.crm.add_or_update_lead({
            "batch_id": self.batch["id"],
            "slug": "mismatched-bakery-31",
            "name": "Ardent Bakery & Pastry",
            "address": "31 High St, London",
            "phone": "+44 20 7946 0031",
            "website": "https://ardentbakery.co.uk",
            "rating": 4.9,
            "review_count": 40,
            "google_category": "Bakery",
            "status": "qualified",
            "score_total": 70.0
        })
        self.crm.add_or_update_lead({
            "batch_id": self.batch["id"],
            "slug": "mismatched-pub-32",
            "name": "The Royal Oak Pub",
            "address": "32 High St, London",
            "phone": "+44 20 7946 0032",
            "website": "https://royaloak.co.uk",
            "rating": 4.7,
            "review_count": 55,
            "google_category": "Pub",
            "status": "qualified",
            "score_total": 70.0
        })

        # 33: Faulty lead that raises a simulated downstream exception
        self.crm.add_or_update_lead({
            "batch_id": self.batch["id"],
            "slug": "exploding-shop-33",
            "name": "Exploding Faulty Garage",
            "address": "33 Crash Lane, London",
            "phone": "+44 20 7946 0033",
            "website": "https://explodingshop.co.uk",
            "rating": 4.5,
            "review_count": 20,
            "google_category": "Car detailing service",
            "status": "qualified",
            "score_total": 65.0
        })

        def mock_scout(name, address):
            if "Exploding Faulty" in name:
                raise RuntimeError("Simulated unrecoverable hardware / scraping failure")
            return {
                "status": "scouted",
                "business_name": name,
                "category": "Car detailing service",
                "categories": ["Car detailing service"],
                "rating": 4.8,
                "review_count": 25,
                "phone": "+44 20 7946 0000",
                "address": address,
                "website": "https://example.co.uk",
                "hours": "Mon-Fri 09:00 - 18:00",
                "photos": [],
                "reviews": [{"author": "John D.", "stars": "5.0★", "text": "Superb paint protection and courteous service."}],
                "services": [{"title": "Ceramic Coating", "desc": None, "price": None, "category": "Services"}]
            }

        res = asyncio.run(
            self.worker.build_batch(
                self.batch["id"],
                config={"max_concurrent_builds": 4, "pause_between_leads_sec": 0},
                mock_scout_provider=mock_scout,
                mock_screenshot_provider=mock_screenshot_generator
            )
        )

        self.assertEqual(res["total_processed"], 33)
        self.assertEqual(res["built_count"], 28)
        self.assertEqual(res["needs_confirmation_count"], 4)
        self.assertEqual(res["failed_count"], 1)

        # Check CONFIRM.json was written for domain mismatch and ambiguous leads
        for slug in ["mismatched-bakery-31", "mismatched-pub-32", "ambiguous-enterprise-29", "ambiguous-enterprise-30"]:
            conf_file = os.path.join(self.demos_dir.name, slug, "CONFIRM.json")
            self.assertTrue(os.path.exists(conf_file), f"CONFIRM.json missing for {slug}")
            with open(conf_file, "r", encoding="utf-8") as f:
                conf_data = json.load(f)
            self.assertEqual(conf_data["slug"], slug)

        # Check CRM statuses
        built_leads = self.crm.get_leads_by_batch(self.batch["id"], status="built")
        self.assertEqual(len(built_leads), 28)

        unconfirmed = self.crm.get_leads_by_batch(self.batch["id"], status="needs_confirmation")
        self.assertEqual(len(unconfirmed), 4)

        failed = self.crm.get_lead("exploding-shop-33")
        self.assertEqual(failed["status"], "error")

        # Test Idempotency: Re-running worker doesn't rebuild finished leads
        res_rerun = asyncio.run(
            self.worker.build_batch(
                self.batch["id"],
                config={"max_concurrent_builds": 4, "pause_between_leads_sec": 0},
                mock_scout_provider=mock_scout,
                mock_screenshot_provider=mock_screenshot_generator
            )
        )
        # Should process 0 candidates since built leads are skipped
        self.assertEqual(res_rerun["total_processed"], 0)
        self.assertEqual(res_rerun["built_count"], 28)

    def test_deploy_gate_requires_tactical_authorization(self):
        """Deploying a batch never happens without tactical authorization and can deselect leads."""
        # Create 2 built leads
        self.crm.add_or_update_lead({
            "batch_id": self.batch["id"],
            "slug": "deployable-shop-1",
            "name": "Deployable Shop 1",
            "phone": "+44 20 7946 0001",
            "status": "built"
        })
        self.crm.add_or_update_lead({
            "batch_id": self.batch["id"],
            "slug": "deployable-shop-2",
            "name": "Deployable Shop 2",
            "phone": "+44 20 7946 0002",
            "status": "built"
        })

        # 1. Authorization Denied
        with patch("ghost.workers.batch_demo_worker.request_tactical_authorization", new_callable=AsyncMock) as m_auth:
            m_auth.return_value = False
            res = asyncio.run(self.worker.deploy_batch(self.batch["id"]))
            self.assertEqual(res["status"], "authorization_denied")
            self.assertEqual(res["deployed_count"], 0)

        # Verify leads are still 'built'
        l1 = self.crm.get_lead("deployable-shop-1")
        self.assertEqual(l1["status"], "built")

        # 2. Authorization Allowed + Deselection of lead 2
        def mock_deploy(slug, project_name):
            return {
                "status": "success",
                "url": f"https://{project_name}.vercel.app"
            }

        with patch("ghost.workers.batch_demo_worker.request_tactical_authorization", new_callable=AsyncMock) as m_auth:
            m_auth.return_value = True
            res = asyncio.run(
                self.worker.deploy_batch(
                    self.batch["id"],
                    deselect_slugs=["deployable-shop-2"],
                    mock_deployer=mock_deploy
                )
            )
            self.assertEqual(res["status"], "batch_deployed")
            self.assertEqual(res["deployed_count"], 1)
            self.assertEqual(res["deployments"][0]["slug"], "deployable-shop-1")
            self.assertEqual(res["deployments"][0]["url"], "https://deployable-shop-1-concept.vercel.app")

        # Verify CRM updates: lead 1 is deployed, lead 2 is still built
        l1_updated = self.crm.get_lead("deployable-shop-1")
        self.assertEqual(l1_updated["status"], "deployed")
        self.assertEqual(l1_updated["demo_url"], "https://deployable-shop-1-concept.vercel.app")

        l2_still_built = self.crm.get_lead("deployable-shop-2")
        self.assertEqual(l2_still_built["status"], "built")
        self.assertIsNone(l2_still_built.get("demo_url"))


if __name__ == "__main__":
    unittest.main()
