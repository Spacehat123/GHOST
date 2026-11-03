"""Outreach Copy & Persona Firewall Purity Tests (Part 4)."""
import os
import re
import tempfile
import unittest

from ghost.crm.db import CRMDatabase
from ghost.workers.demo_state_machine import BANNED_OUTBOUND_TERMS, sanitize_outbound
from ghost.workers.outreach_drafter import (
    OutreachDrafter,
    get_word_count,
    DEFAULT_OPT_OUT_LINE,
    HONEST_FRAMING_LINE
)


class TestOutreachPurity(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test_crm.db")
        self.crm = CRMDatabase(db_path=self.db_path)
        self.drafter = OutreachDrafter(crm=self.crm)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_all_pitch_types_generate_pure_copy_meeting_all_constraints(self):
        """All pitch types (no_site, weak_site, no_booking_path) produce pure copy meeting all strict rules."""
        pitch_scenarios = [
            ("no_site", None, 0.0),
            ("weak_site", "https://slowsite.co.uk", 25.0),
            ("no_booking_path", "https://fastsite.co.uk", 10.0)
        ]

        for p_type, site, friction_score in pitch_scenarios:
            slug = f"prospect-{p_type}"
            lead = self.crm.add_or_update_lead({
                "slug": slug,
                "name": f"Elite {p_type.replace('_', ' ').title()} Studio Ltd",
                "address": "40 King Street, London W1",
                "phone": "+44 7911 111222",
                "email": f"info@{slug}.co.uk",
                "website": site,
                "entity_type": "limited_company",
                "score_friction": friction_score,
                "pitch_type": p_type,
                "demo_url": f"https://{slug}-concept.vercel.app",
                "status": "deployed"
            })

            res = self.drafter.create_drafts_for_lead(
                slug,
                config={
                    "operator_name": "Pranav",
                    "operator_business_name": "Bespoke Design",
                    "postal_address": "London, UK"
                }
            )

            self.assertEqual(res["status"], "drafts_ready")
            self.assertGreaterEqual(res["draft_count"], 2)

            for d in res["drafts"]:
                channel = d["channel"]
                body = d["body"]
                link = d.get("link")

                # 1. Passes sanitize_outbound without exception
                sanitize_outbound(body)
                if d.get("subject"):
                    sanitize_outbound(d["subject"])

                # 2. Zero internal persona terms
                for term in BANNED_OUTBOUND_TERMS:
                    pat = r'\b' + re.escape(term.lower()) + r'\b' if term.isalnum() else re.escape(term.lower())
                    self.assertIsNone(
                        re.search(pat, body, flags=re.IGNORECASE),
                        f"Found banned persona term '{term}' in {channel} draft!"
                    )

                # 3. Zero local paths or file://
                self.assertNotIn("file://", body)
                self.assertNotIn("/home/", body)
                if link:
                    self.assertNotIn("file://", link)
                    self.assertNotIn("/home/", link)

                # 4. Mandatory Live Concept URL
                self.assertIn(f"https://{slug}-concept.vercel.app", body)

                # 5. Mandatory Honest Framing Line
                self.assertIn(HONEST_FRAMING_LINE, body)

                # 6. Mandatory Opt-Out Line
                self.assertIn(DEFAULT_OPT_OUT_LINE, body)

                # 7. Word Count Limits
                word_count = get_word_count(body)
                if channel == "whatsapp":
                    self.assertLessEqual(
                        word_count, 60,
                        f"WhatsApp draft for {p_type} exceeded 60 words: {word_count} words"
                    )
                elif channel == "email":
                    self.assertLessEqual(
                        word_count, 120,
                        f"Email draft for {p_type} exceeded 120 words: {word_count} words"
                    )


if __name__ == "__main__":
    unittest.main()
