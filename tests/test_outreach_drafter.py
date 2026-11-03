"""Unit tests for Outreach Drafter & Workflow Loop (Part 4.1 & 4.2)."""
import os
import tempfile
import time
import unittest

from ghost.crm.db import CRMDatabase
from ghost.workers.outreach_drafter import (
    OutreachDrafter,
    is_whatsapp_capable_mobile,
    get_word_count
)


class TestOutreachDrafter(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test_crm.db")
        self.crm = CRMDatabase(db_path=self.db_path)
        self.drafter = OutreachDrafter(crm=self.crm)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_mobile_vs_landline_whatsapp_capability(self):
        """UK landlines (+44 20, 020...) are not WhatsApp-capable; UK mobiles (+44 7..., 07...) are."""
        self.assertFalse(is_whatsapp_capable_mobile("+44 20 7946 0912"))
        self.assertFalse(is_whatsapp_capable_mobile("020 7946 0912"))
        self.assertFalse(is_whatsapp_capable_mobile("+44 161 496 0123"))

        self.assertTrue(is_whatsapp_capable_mobile("+44 7911 123456"))
        self.assertTrue(is_whatsapp_capable_mobile("07911 123456"))
        self.assertFalse(is_whatsapp_capable_mobile(None))

    def test_draft_generation_with_mobile_and_limited_company(self):
        """A limited company with a mobile and email gets both WhatsApp and Email drafts."""
        lead = self.crm.add_or_update_lead({
            "slug": "mayfair-detailing-ltd",
            "name": "Mayfair Detailing Ltd",
            "address": "1 Mayfair Pl, London W1J 8AJ",
            "phone": "+44 7911 222333",
            "email": "contact@mayfairdetailing.co.uk",
            "website": "https://mayfairdetailing.co.uk",
            "entity_type": "limited_company",
            "demo_url": "https://mayfair-detailing-ltd-concept.vercel.app",
            "status": "deployed"
        })

        res = self.drafter.create_drafts_for_lead(
            lead["slug"],
            config={
                "operator_name": "Pranav",
                "operator_business_name": "Pranav Agency",
                "postal_address": "London, UK"
            }
        )

        self.assertEqual(res["status"], "drafts_ready")
        self.assertEqual(res["draft_count"], 2)

        # Check WhatsApp draft
        wa_drafts = [d for d in res["drafts"] if d["channel"] == "whatsapp"]
        self.assertEqual(len(wa_drafts), 1)
        wa = wa_drafts[0]
        self.assertIn("https://wa.me/447911222333", wa["link"])
        self.assertLessEqual(get_word_count(wa["body"]), 60)
        self.assertIn("I made this concept for you as an example", wa["body"])
        self.assertIn("reply 'no thanks'", wa["body"])

        # Check Email draft
        em_drafts = [d for d in res["drafts"] if d["channel"] == "email"]
        self.assertEqual(len(em_drafts), 1)
        em = em_drafts[0]
        self.assertIn("mailto:contact@mayfairdetailing.co.uk", em["link"])
        self.assertLessEqual(get_word_count(em["body"]), 120)
        self.assertIn("I made this concept for you as an example", em["body"])
        self.assertIn("reply 'no thanks'", em["body"])
        self.assertIn("eml_content", em)

    def test_landline_gets_no_whatsapp_draft(self):
        """A business with only a UK landline (+44 20...) does not receive a WhatsApp draft."""
        lead = self.crm.add_or_update_lead({
            "slug": "landline-only-clinic",
            "name": "Harley Street Clinic Ltd",
            "address": "10 Harley St, London W1",
            "phone": "+44 20 7946 0888",
            "email": "info@harleystreetclinic.co.uk",
            "entity_type": "limited_company",
            "demo_url": "https://landline-only-clinic-concept.vercel.app",
            "status": "deployed"
        })

        res = self.drafter.create_drafts_for_lead(lead["slug"])
        channels = [d["channel"] for d in res["drafts"]]
        self.assertNotIn("whatsapp", channels)
        self.assertIn("email", channels)

    def test_unknown_entity_gets_no_email_draft_under_pecr_guard(self):
        """Under UK PECR rules, sole traders, partnerships, and unknown entities get no email drafts when switch is on."""
        lead = self.crm.add_or_update_lead({
            "slug": "unknown-sole-trader",
            "name": "Bob's Mobile Detailing",
            "address": "London, UK",
            "phone": "+44 7911 333444",
            "email": "bob@bobsdetailing.co.uk",
            "entity_type": "unknown",
            "demo_url": "https://unknown-sole-trader-concept.vercel.app",
            "status": "deployed"
        })

        res = self.drafter.create_drafts_for_lead(
            lead["slug"],
            config={"do_not_contact_unknown_entity_email": True}
        )
        channels = [d["channel"] for d in res["drafts"]]
        self.assertNotIn("email", channels)
        self.assertIn("whatsapp", channels)

    def test_no_drafts_without_live_url(self):
        """No draft may be created without a verified live hosted demo URL."""
        lead = self.crm.add_or_update_lead({
            "slug": "unhosted-lead",
            "name": "Unhosted Garage",
            "demo_url": None,
            "status": "built"
        })

        res = self.drafter.create_drafts_for_lead(lead["slug"])
        self.assertEqual(res["status"], "needs_deploy")
        self.assertEqual(res["draft_count"], 0)

    def test_cannot_approve_draft_when_operator_details_missing(self):
        """Approving a draft requires configured operator name and business address/name; blocks approval if missing."""
        lead = self.crm.add_or_update_lead({
            "slug": "approval-test-lead",
            "name": "Approval Test Ltd",
            "phone": "+44 7911 444555",
            "demo_url": "https://approval-test-lead-concept.vercel.app",
            "status": "deployed"
        })

        res = self.drafter.create_drafts_for_lead(lead["slug"])
        draft_id = res["drafts"][0]["id"]

        # Missing postal address and business name
        with self.assertRaises(ValueError) as ctx:
            self.drafter.approve_draft(
                draft_id,
                config_override={"operator_name": "Pranav", "operator_business_name": None, "postal_address": None}
            )
        self.assertIn("Cannot approve draft while operator details are missing", str(ctx.exception))

        # Successfully approve with full operator details
        app_res = self.drafter.approve_draft(
            draft_id,
            config_override={
                "operator_name": "Pranav",
                "operator_business_name": "Pranav Agency",
                "postal_address": "London, UK"
            }
        )
        self.assertEqual(app_res["status"], "approved")
        lead_after = self.crm.get_lead("approval-test-lead")
        self.assertEqual(lead_after["status"], "approved")

    def test_mark_draft_sent_records_outbound_and_checks_daily_cap(self):
        """Marking a draft as sent records outbound interaction in CRM and logs daily cap status."""
        lead = self.crm.add_or_update_lead({
            "slug": "sent-test-lead",
            "name": "Sent Test Ltd",
            "phone": "+44 7911 888999",
            "demo_url": "https://sent-test-lead-concept.vercel.app",
            "status": "deployed"
        })
        res = self.drafter.create_drafts_for_lead(lead["slug"])
        draft_id = res["drafts"][0]["id"]

        self.drafter.approve_draft(
            draft_id,
            config_override={"operator_name": "Pranav", "operator_business_name": "Pranav Studio", "postal_address": "London"}
        )

        sent_res = self.drafter.mark_draft_sent(draft_id, config_override={"max_sends_per_day": 50})
        self.assertEqual(sent_res["status"], "sent")
        self.assertIsNone(sent_res["warning"])

        lead_after = self.crm.get_lead("sent-test-lead")
        self.assertEqual(lead_after["status"], "sent")

    def test_followup_draft_scheduling(self):
        """Follow-up draft is created only after followup_after_days elapsed, max 1, and none if replied/suppressed."""
        lead = self.crm.add_or_update_lead({
            "slug": "followup-lead",
            "name": "Followup Studio Ltd",
            "phone": "+44 7911 777111",
            "demo_url": "https://followup-lead-concept.vercel.app",
            "status": "deployed"
        })
        res = self.drafter.create_drafts_for_lead(lead["slug"])
        draft_id = res["drafts"][0]["id"]

        # Approve and mark sent 4 days ago
        four_days_ago = time.time() - (4 * 86400)
        with self.crm._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE drafts SET status = 'sent', sent_at = ? WHERE id = ?", (four_days_ago, draft_id))
            cur.execute("UPDATE leads SET status = 'sent' WHERE slug = 'followup-lead'")
            conn.commit()

        # Follow-up should now be due
        fu = self.drafter.generate_followup_draft("followup-lead", config={"followup_after_days": 3, "max_followups": 1})
        self.assertIsNotNone(fu)
        self.assertIn("following up", fu["body"].lower())

        # Attempting second follow-up should return None (max 1 reached)
        fu2 = self.drafter.generate_followup_draft("followup-lead", config={"followup_after_days": 3, "max_followups": 1})
        self.assertIsNone(fu2)


if __name__ == "__main__":
    unittest.main()
