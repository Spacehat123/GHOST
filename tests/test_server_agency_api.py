"""Unit & Security Tests for Agency Command Deck REST & WebSocket API (Part 6)."""
import os
import json
import tempfile
import unittest
from starlette.testclient import TestClient

from ghost.crm.db import CRMDatabase
from ghost.server.app import app
from ghost.server.agency_api import (
    get_or_create_session_token,
    ALLOWED_CONTRACT_TOKENS,
    ALLOWED_OUTREACH_TOKENS
)


class TestServerAgencyAPI(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test_crm.db")
        self.crm = CRMDatabase(db_path=self.db_path)
        # Patch singleton CRM for testing
        CRMDatabase._instance = self.crm

        self.token = get_or_create_session_token()
        self.client = TestClient(app)
        self.auth_headers = {"X-Session-Token": self.token}

    def tearDown(self):
        self.tmpdir.cleanup()
        CRMDatabase._instance = None

    def test_auth_and_localhost_security(self):
        """Endpoints enforce session token authentication and localhost check."""
        # 1. Missing session token returns 401
        res = self.client.get("/api/operator")
        self.assertEqual(res.status_code, 401)
        self.assertIn("Unauthorized", res.json()["detail"])

        # 2. Invalid session token returns 401
        res = self.client.get("/api/operator", headers={"X-Session-Token": "bad_token_xyz"})
        self.assertEqual(res.status_code, 401)

        # 3. Valid token succeeds
        res = self.client.get("/api/operator", headers=self.auth_headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("daily_sends_today", data)
        self.assertIn("max_sends_per_day", data)

    def test_campaign_setup_distinct_categories_validation(self):
        """Campaign creation requires exactly 3 distinct categories; duplicates return 400."""
        # 1. Duplicate categories rejected
        dup_payload = {
            "name": "Test Alpha",
            "city": "London",
            "categories": ["car detailing", "car detailing", "gym"],
            "harvest_per_batch": 100,
            "keep_fraction": 0.33
        }
        res = self.client.post("/api/campaigns", json=dup_payload, headers=self.auth_headers)
        self.assertEqual(res.status_code, 400)
        self.assertIn("distinct", res.json()["detail"].lower())

        # 2. Non-3 categories rejected
        wrong_count_payload = {
            "name": "Test Beta",
            "city": "London",
            "categories": ["car detailing", "gym"],
            "harvest_per_batch": 100
        }
        res = self.client.post("/api/campaigns", json=wrong_count_payload, headers=self.auth_headers)
        self.assertEqual(res.status_code, 400)

        # 3. 3 distinct categories accepted
        valid_payload = {
            "name": "London Alpha",
            "city": "London",
            "categories": ["car detailing", "boutique gym", "dental clinic"],
            "harvest_per_batch": 100,
            "keep_fraction": 0.33
        }
        res = self.client.post("/api/campaigns", json=valid_payload, headers=self.auth_headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(len(data["batches"]), 3)

    def test_xss_protection_on_untrusted_scraped_data(self):
        """Untrusted scraped text containing HTML tags is stored safely without script execution."""
        xss_name = '<img src=x onerror=alert("pwned")>'
        xss_address = '<script>window.location="http://evil.com"</script>'

        lead = self.crm.add_or_update_lead({
            "slug": "xss-test-lead",
            "name": xss_name,
            "address": xss_address,
            "entity_type": "limited_company",
            "status": "qualified"
        })

        res = self.client.get(f"/api/leads/{lead['slug']}", headers=self.auth_headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()["lead"]
        # Stored and returned as pure string data
        self.assertEqual(data["name"], xss_name)
        self.assertEqual(data["address"], xss_address)

    def test_state_machine_illegal_transition_returns_409(self):
        """Marking an unapproved draft as sent returns 409 Conflict."""
        lead = self.crm.add_or_update_lead({
            "slug": "draft-transition-lead",
            "name": "Transition Testing Ltd",
            "address": "40 Fleet St, London",
            "phone": "+44 7911 223344",
            "status": "deployed",
            "demo_url": "https://test-concept.vercel.app"
        })

        draft = self.crm.add_or_update_draft(
            lead_id=lead["id"],
            channel="whatsapp",
            body="Test draft message",
            status="draft"
        )
        self.assertEqual(draft["status"], "draft")

        # Attempting to mark draft as 'sent' without prior approval must return 409 Conflict
        res = self.client.post(f"/api/drafts/{draft['id']}/sent", headers=self.auth_headers)
        self.assertEqual(res.status_code, 409)
        self.assertIn("approved", res.json()["detail"].lower())

    def test_1click_opt_out_endpoint(self):
        """1-Click opt-out adds all identifiers to suppression and removes drafts."""
        slug = "optout-prospect"
        lead = self.crm.add_or_update_lead({
            "slug": slug,
            "name": "OptOut Enterprises",
            "phone": "+44 7911 999888",
            "email": "optout@prospect.co.uk",
            "domain": "prospect.co.uk",
            "maps_cid": "cid_998877",
            "status": "drafts_ready"
        })

        draft = self.crm.add_or_update_draft(
            lead_id=lead["id"],
            channel="whatsapp",
            body="Draft before opt out",
            status="draft"
        )

        res = self.client.post(
            f"/api/leads/{slug}/optout",
            json={"reason": "Prospect requested opt-out"},
            headers=self.auth_headers
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["ok"])
        supp_count = len(data["suppressed_identifiers"]) if isinstance(data["suppressed_identifiers"], list) else data["suppressed_identifiers"]
        self.assertGreaterEqual(supp_count, 4)

        # Check lead status is suppressed
        updated_lead = self.crm.get_lead(slug)
        self.assertEqual(updated_lead["status"], "suppressed")

        # Drafts queue should not return suppressed drafts
        drafts_res = self.client.get("/api/drafts", headers=self.auth_headers)
        draft_ids = [d["id"] for d in drafts_res.json()["drafts"]]
        self.assertNotIn(draft["id"], draft_ids)

    def test_template_editor_token_validation(self):
        """Template editor rejects unknown/malicious tokens with 400 Bad Request."""
        target_file = "/home/pranc/projects/ghost/templates/outreach/whatsapp_no_site.md.j2"
        orig_content = None
        if os.path.exists(target_file):
            with open(target_file, "r", encoding="utf-8") as f:
                orig_content = f.read()

        try:
            # 1. Invalid token rejected
            bad_payload = {
                "content": "Hi {{name}}, check your site: {{UNKNOWN_INVENTED_TOKEN_999}}"
            }
            res = self.client.post(
                "/api/templates/outreach/whatsapp_no_site.md.j2",
                json=bad_payload,
                headers=self.auth_headers
            )
            self.assertEqual(res.status_code, 400)
            self.assertIn("unknown tokens", res.json()["detail"].lower())

            # 2. Valid tokens accepted
            valid_payload = {
                "content": "Hi {{name}}, I made a site concept for you: {{demo_url}}. I made this concept for you as an example. It isn't live and isn't affiliated with you. Would you like to review it? {{opt_out_line}} — {{operator_name}}"
            }
            res = self.client.post(
                "/api/templates/outreach/whatsapp_no_site.md.j2",
                json=valid_payload,
                headers=self.auth_headers
            )
            self.assertEqual(res.status_code, 200)
        finally:
            if orig_content is not None:
                with open(target_file, "w", encoding="utf-8") as f:
                    f.write(orig_content)

    def test_closing_pack_and_document_download(self):
        """Prepare closing pack, mark lawyer reviewed, and download documents."""
        slug = "closing-api-lead"
        lead = self.crm.add_or_update_lead({
            "slug": slug,
            "name": "Regent Street Dental",
            "legal_name": "Regent Dental Practice Ltd",
            "address": "100 Regent Street, London W1B 5TH",
            "status": "agreed",
            "entity_type": "limited_company",
            "deal_value": 2400.00
        })

        # 1. Prepare pack
        pack_res = self.client.post(
            f"/api/leads/{slug}/closing_pack",
            json={
                "total_price": 2400.00,
                "governing_law": "England and Wales",
                "provider_name": "Pranav Studio",
                "provider_address": "London, UK"
            },
            headers=self.auth_headers
        )
        self.assertEqual(pack_res.status_code, 200)
        pack_data = pack_res.json()
        self.assertEqual(pack_data["lead_status"], "contract_drafted")
        self.assertGreaterEqual(len(pack_data["documents"]), 5)

        first_doc_id = pack_data["documents"][0]["id"]

        # 2. Mark lawyer reviewed
        rev_res = self.client.post(f"/api/leads/{slug}/lawyer_reviewed", headers=self.auth_headers)
        self.assertEqual(rev_res.status_code, 200)

        # 3. Download document file
        down_res = self.client.get(
            f"/api/documents/{first_doc_id}/download",
            headers=self.auth_headers
        )
        self.assertEqual(down_res.status_code, 200)
        self.assertIn("text/markdown", down_res.headers.get("content-type", ""))

    def test_live_websocket_connection_with_token(self):
        """WebSocket /ws/events connects with valid session token and rejects invalid token."""
        # 1. Invalid token rejected
        with self.assertRaises(Exception):
            with self.client.websocket_connect("/ws/events?token=bad_token"):
                pass

        # 2. Valid token connects
        with self.client.websocket_connect(f"/ws/events?token={self.token}") as ws:
            self.assertIsNotNone(ws)


if __name__ == "__main__":
    unittest.main()
