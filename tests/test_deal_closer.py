"""Unit Tests for Deal Closer & Contract Generation (Part 5)."""
from decimal import Decimal
import os
import shutil
import tempfile
import unittest

import docx

from ghost.crm.db import CRMDatabase
from ghost.workers.deal_closer import (
    DealCloser,
    split_payment_decimals,
    get_currency_and_symbol
)


class TestDealCloser(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test_crm.db")
        self.crm = CRMDatabase(db_path=self.db_path)
        self.closer = DealCloser(crm=self.crm)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_deposit_balance_arithmetic_exact_sum(self):
        """Decimal arithmetic: deposit/balance split always sums exactly to total."""
        # 1. 1,201 at 50% -> 600.50 / 600.50
        dep, bal = split_payment_decimals("1201.00", 50)
        self.assertEqual(dep, Decimal("600.50"))
        self.assertEqual(bal, Decimal("600.50"))
        self.assertEqual(dep + bal, Decimal("1201.00"))

        # 2. Odd pence: 1,200.01 at 50% -> 600.01 / 600.00
        dep, bal = split_payment_decimals("1200.01", 50)
        self.assertEqual(dep, Decimal("600.01"))
        self.assertEqual(bal, Decimal("600.00"))
        self.assertEqual(dep + bal, Decimal("1200.01"))

        # 3. 1,000 at 33% -> 330.00 / 670.00
        dep, bal = split_payment_decimals("1000.00", 33)
        self.assertEqual(dep, Decimal("330.00"))
        self.assertEqual(bal, Decimal("670.00"))
        self.assertEqual(dep + bal, Decimal("1000.00"))

        # 4. Zero total
        dep, bal = split_payment_decimals("0.00", 50)
        self.assertEqual(dep, Decimal("0.00"))
        self.assertEqual(bal, Decimal("0.00"))
        self.assertEqual(dep + bal, Decimal("0.00"))

    def test_currency_symbol_follows_client_country_and_unknown(self):
        """Currency symbol follows the client's country; no symbol when currency unknown."""
        # UK / GBP
        cur, sym = get_currency_and_symbol("GBP")
        self.assertEqual(cur, "GBP")
        self.assertEqual(sym, "£")

        cur, sym = get_currency_and_symbol(address_or_country="221B Baker St, London, UK")
        self.assertEqual(cur, "GBP")
        self.assertEqual(sym, "£")

        # US / USD
        cur, sym = get_currency_and_symbol("USD")
        self.assertEqual(cur, "USD")
        self.assertEqual(sym, "$")

        # EU / EUR
        cur, sym = get_currency_and_symbol("EUR")
        self.assertEqual(cur, "EUR")
        self.assertEqual(sym, "€")

        # India / INR
        cur, sym = get_currency_and_symbol("INR")
        self.assertEqual(cur, "INR")
        self.assertEqual(sym, "₹")

        # Unknown currency -> no symbol
        cur, sym = get_currency_and_symbol("XYZ")
        self.assertEqual(cur, "XYZ")
        self.assertIsNone(sym)

        cur, sym = get_currency_and_symbol(None, None)
        self.assertIsNone(cur)
        self.assertIsNone(sym)

    def test_missing_required_values_block_ready_state(self):
        """Missing required legal/money values keep the document in blocked/draft state."""
        slug = "incomplete-lead"
        self.crm.add_or_update_lead({
            "slug": slug,
            "name": "Incomplete Works",
            "status": "agreed",
            "entity_type": "sole_trader"
            # Missing address, price, legal name
        })

        res = self.closer.prepare_closing_pack(
            slug,
            overrides={
                "governing_law": None,
                "total_price": None,
                "provider_name": "Pranav Studio",
                "provider_address": None
            }
        )

        self.assertEqual(res["status"], "blocked")
        self.assertIn("TOTAL_PRICE", res["missing_fields"])
        self.assertIn("GOVERNING_LAW", res["missing_fields"])
        self.assertIn("CLIENT_LEGAL_NAME", res["missing_fields"])
        self.assertIn("CLIENT_ADDRESS", res["missing_fields"])

        # Check that documents contain [[MISSING: ...]] tokens
        docs = self.crm.get_documents_for_lead(self.crm.get_lead(slug)["id"])
        self.assertGreaterEqual(len(docs), 5)
        for d in docs:
            self.assertEqual(d["status"], "draft")
            with open(d["path"], "r", encoding="utf-8") as f:
                content = f.read()
            if d["kind"] == "contract":
                self.assertIn("[[MISSING: TOTAL_PRICE]]", content)
                self.assertIn("[[MISSING: GOVERNING_LAW]]", content)

        # Attempting to mark lawyer-reviewed fails while missing tokens remain
        with self.assertRaises(ValueError):
            self.closer.mark_lawyer_reviewed(slug)

    def test_watermark_present_until_lawyer_reviewed(self):
        """Watermark header says DRAFT until lawyer_reviewed=True."""
        slug = "complete-lead"
        self.crm.add_or_update_lead({
            "slug": slug,
            "name": "Apex Auto Care Ltd",
            "legal_name": "Apex Auto Care Limited",
            "address": "12 High Street, Birmingham B1 1AA, UK",
            "email": "contact@apexautocare.co.uk",
            "status": "agreed",
            "entity_type": "limited_company",
            "deal_value": 1500.00
        })

        # 1. Initially generated as draft
        res = self.closer.prepare_closing_pack(
            slug,
            overrides={
                "total_price": 1500.00,
                "governing_law": "England and Wales",
                "provider_name": "Pranav Studio",
                "provider_address": "London, UK",
                "bank_name": "Barclays",
                "sort_code": "20-00-00",
                "account_number": "12345678"
            },
            lawyer_reviewed=False
        )
        self.assertEqual(res["status"], "draft")
        self.assertEqual(len(res["missing_fields"]), 0)

        docs = self.crm.get_documents_for_lead(self.crm.get_lead(slug)["id"])
        contract_doc = next(d for d in docs if d["kind"] == "contract" and "one_page" in d["path"])
        with open(contract_doc["path"], "r", encoding="utf-8") as f:
            c1 = f.read()
        self.assertIn("DRAFT - not reviewed. Do not send.", c1)
        self.assertNotIn("REVIEWED - Approved for sending.", c1)

        # 2. Mark lawyer-reviewed
        rev_res = self.closer.mark_lawyer_reviewed(slug)
        self.assertEqual(rev_res["status"], "lawyer_reviewed")

        with open(contract_doc["path"], "r", encoding="utf-8") as f:
            c2 = f.read()
        self.assertIn("REVIEWED - Approved for sending.", c2)
        self.assertNotIn("DRAFT - not reviewed. Do not send.", c2)

    def test_handover_checklist_generation(self):
        """HANDOVER_CHECKLIST.md lists missing fields and all 5 advisory flags."""
        slug = "checklist-lead"
        self.crm.add_or_update_lead({
            "slug": slug,
            "name": "Prestige Dental",
            "legal_name": "Prestige Dental Care LLP",
            "address": "44 Harley Street, London W1G 9PR",
            "status": "agreed",
            "entity_type": "partnership"
        })

        res = self.closer.prepare_closing_pack(
            slug,
            overrides={
                "total_price": 2000.00,
                "governing_law": "England and Wales",
                "provider_name": "Pranav Studio",
                "provider_address": "London, UK"
            }
        )

        checklist_path = res["checklist_path"]
        self.assertTrue(os.path.exists(checklist_path))
        with open(checklist_path, "r", encoding="utf-8") as f:
            chk = f.read()

        # Check 5 flags are present
        self.assertIn("Flag 1: Non-Refundable Deposit Policy", chk)
        self.assertIn("Flag 2: Client Entity Type", chk)
        self.assertIn("partnership", chk)
        self.assertIn("Flag 3: Foreign Clients, Currency & Tax Treatment", chk)
        self.assertIn("Flag 4: Intellectual Property & Concept Staging Retention", chk)
        self.assertIn("Flag 5: Data Protection & Privacy Notice", chk)

    def test_pipeline_progression_from_agreed_to_won(self):
        """Pipeline advances: agreed -> contract_drafted -> contract_sent -> deposit_received -> won."""
        slug = "lifecycle-lead"
        lead = self.crm.add_or_update_lead({
            "slug": slug,
            "name": "Solstice Cafe",
            "legal_name": "Solstice Cafe Ltd",
            "address": "7 Church Lane, Bristol BS1 2AA",
            "status": "agreed",
            "entity_type": "limited_company",
            "deal_value": 1200.00
        })
        self.assertEqual(lead["status"], "agreed")

        # Step 1: Prepare pack -> contract_drafted
        pack_res = self.closer.prepare_closing_pack(
            slug,
            overrides={
                "total_price": 1200.00,
                "governing_law": "England and Wales",
                "provider_name": "Pranav Studio",
                "provider_address": "London, UK"
            }
        )
        self.assertEqual(pack_res["lead_status"], "contract_drafted")
        l1 = self.crm.get_lead(slug)
        self.assertEqual(l1["status"], "contract_drafted")

        # Step 2: Mark contract sent -> contract_sent
        sent_res = self.closer.mark_contract_sent(slug)
        self.assertEqual(sent_res["status"], "contract_sent")
        l2 = self.crm.get_lead(slug)
        self.assertEqual(l2["status"], "contract_sent")

        # Step 3: Mark deposit received -> deposit_received / won
        dep_res = self.closer.mark_deposit_received(slug)
        self.assertEqual(dep_res["status"], "won")
        l3 = self.crm.get_lead(slug)
        self.assertEqual(l3["status"], "won")

        # Verify audit trail events
        events = self.crm.get_events(lead_id=l3["id"])
        event_types = [e["type"] for e in events]
        self.assertIn("contract_drafted", event_types)
        self.assertIn("contract_sent", event_types)
        self.assertIn("deposit_received", event_types)
        self.assertIn("won", event_types)

    def test_docx_file_generation(self):
        """Verifies valid DOCX files are created alongside Markdown."""
        slug = "docx-lead"
        self.crm.add_or_update_lead({
            "slug": slug,
            "name": "Glow Aesthetics",
            "legal_name": "Glow Aesthetics Ltd",
            "address": "15 Oxford St, London W1D 2DH",
            "status": "agreed",
            "entity_type": "limited_company"
        })

        res = self.closer.prepare_closing_pack(
            slug,
            overrides={
                "total_price": 1800.00,
                "governing_law": "England and Wales",
                "provider_name": "Pranav Studio",
                "provider_address": "London, UK"
            }
        )

        docs = self.crm.get_documents_for_lead(self.crm.get_lead(slug)["id"])
        for d in docs:
            md_path = d["path"]
            if d["kind"] in ("contract", "welcome", "invoice"):
                docx_path = md_path.replace(".md", ".docx")
                self.assertTrue(os.path.exists(docx_path), f"Missing DOCX at {docx_path}")
                # Verify docx is readable
                doc = docx.Document(docx_path)
                self.assertGreater(len(doc.paragraphs), 0)


if __name__ == "__main__":
    unittest.main()
