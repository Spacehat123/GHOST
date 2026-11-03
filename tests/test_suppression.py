"""Unit tests for Suppression & Opt-Out System (Part 4.3)."""
import os
import tempfile
import unittest

from ghost.crm.db import CRMDatabase
from ghost.workers.suppression import SuppressionManager


class TestSuppressionManager(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test_crm.db")
        self.crm = CRMDatabase(db_path=self.db_path)
        self.manager = SuppressionManager(crm=self.crm)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_add_and_lookup_by_kind(self):
        """Identifiers of various kinds (email, phone, domain, maps_cid, name) are properly normalized and matched."""
        # 1. Email normalization (case insensitivity)
        self.manager.add("email", "Client@Example.COM", "Opt-out requested")
        self.assertTrue(self.manager.is_suppressed(kind="email", value="client@example.com"))
        self.assertTrue(self.manager.is_suppressed(kind="email", value="CLIENT@EXAMPLE.COM"))
        self.assertFalse(self.manager.is_suppressed(kind="email", value="other@example.com"))

        # 2. Phone normalization (E.164 digits)
        self.manager.add("phone", "+44 20 7946 0999", "DNC requested")
        self.assertTrue(self.manager.is_suppressed(kind="phone", value="020 7946 0999"))
        self.assertTrue(self.manager.is_suppressed(kind="phone", value="+442079460999"))
        self.assertFalse(self.manager.is_suppressed(kind="phone", value="+44 20 7946 0001"))

        # 3. Domain normalization (bare domain)
        self.manager.add("domain", "https://www.badcompany.co.uk/about", "Legal cease")
        self.assertTrue(self.manager.is_suppressed(kind="domain", value="badcompany.co.uk"))
        self.assertTrue(self.manager.is_suppressed(kind="domain", value="https://badcompany.co.uk"))
        self.assertFalse(self.manager.is_suppressed(kind="domain", value="goodcompany.co.uk"))

        # 4. Maps CID
        self.manager.add("maps_cid", "cid_123456789", "Blacklisted")
        self.assertTrue(self.manager.is_suppressed(kind="maps_cid", value="cid_123456789"))

    def test_is_suppressed_with_lead_dict(self):
        """Checking a lead dictionary returns True if ANY identifier matches suppression."""
        self.manager.add("phone", "+44 7911 123456", "User opt-out")

        lead_match = {
            "name": "Acme Detailing",
            "phone": "+44 7911 123456",
            "email": "acme@example.com",
            "website": "https://acmedetailing.com"
        }
        self.assertTrue(self.manager.is_suppressed(lead=lead_match))

        lead_clean = {
            "name": "Pristine Detailing",
            "phone": "+44 7911 999999",
            "email": "clean@example.com",
            "website": "https://pristine.com"
        }
        self.assertFalse(self.manager.is_suppressed(lead=lead_clean))

    def test_opt_out_lead_suppresses_all_identifiers_and_cancels_drafts(self):
        """1-click opt-out adds all identifiers to suppression, marks lead suppressed, and cancels active drafts."""
        # Create lead and drafts
        lead = self.crm.add_or_update_lead({
            "slug": "optout-prospect",
            "name": "Optout Prospect Ltd",
            "phone": "+44 7911 555666",
            "email": "optout@prospect.co.uk",
            "website": "https://optoutprospect.co.uk",
            "maps_cid": "cid_optout_99",
            "status": "drafts_ready"
        })

        draft1 = self.crm.add_or_update_draft(
            lead_id=lead["id"],
            channel="whatsapp",
            body="Hello, check out this demo",
            status="draft"
        )
        draft2 = self.crm.add_or_update_draft(
            lead_id=lead["id"],
            channel="email",
            body="Hello, here is a proposal",
            status="approved"
        )

        res = self.manager.opt_out_lead("optout-prospect", reason="Prospect clicked opt-out")
        self.assertEqual(res["status"], "success")
        self.assertEqual(len(res["suppressed_identifiers"]), 5)

        # Check lead status in CRM
        updated_lead = self.crm.get_lead("optout-prospect")
        self.assertEqual(updated_lead["status"], "suppressed")

        # Check drafts are cancelled / marked suppressed
        drafts = self.crm.get_drafts_for_lead(lead["id"])
        for d in drafts:
            self.assertEqual(d["status"], "suppressed")

        # Verify future checks match
        self.assertTrue(self.manager.is_suppressed(kind="email", value="optout@prospect.co.uk"))
        self.assertTrue(self.manager.is_suppressed(kind="phone", value="+44 7911 555666"))
        self.assertTrue(self.manager.is_suppressed(kind="domain", value="optoutprospect.co.uk"))
        self.assertTrue(self.manager.is_suppressed(kind="maps_cid", value="cid_optout_99"))

    def test_detect_opt_out_intent(self):
        """Detects opt-out keywords in reply text."""
        self.assertTrue(self.manager.detect_opt_out_intent("Please stop messaging me"))
        self.assertTrue(self.manager.detect_opt_out_intent("Unsubscribe"))
        self.assertTrue(self.manager.detect_opt_out_intent("No thanks, not interested"))
        self.assertTrue(self.manager.detect_opt_out_intent("Don't contact this number again"))
        self.assertTrue(self.manager.detect_opt_out_intent("Leave me alone please"))
        self.assertTrue(self.manager.detect_opt_out_intent("Take me off your list"))

        # Non-opt-out replies
        self.assertFalse(self.manager.detect_opt_out_intent("Looks interesting, what are your rates?"))
        self.assertFalse(self.manager.detect_opt_out_intent("Can you call me tomorrow at 2pm?"))

    def test_csv_import_and_export(self):
        """CSV import and export works seamlessly."""
        csv_path = os.path.join(self.tmpdir.name, "import.csv")
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write("kind,value,reason\n")
            f.write("email,blocked1@example.com,Customer request\n")
            f.write("phone,+447911000111,Customer request\n")

        imported = self.manager.import_csv(csv_path)
        self.assertEqual(imported, 2)
        self.assertTrue(self.manager.is_suppressed(kind="email", value="blocked1@example.com"))
        self.assertTrue(self.manager.is_suppressed(kind="phone", value="+447911000111"))

        export_path = os.path.join(self.tmpdir.name, "export.csv")
        exported = self.manager.export_csv(export_path)
        self.assertGreaterEqual(exported, 2)
        self.assertTrue(os.path.exists(export_path))


if __name__ == "__main__":
    unittest.main()
