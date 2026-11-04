"""Unit and Contract Tests for Evidence-Based Verification and TaskResult Contract.
Enforces non-negotiable architectural rule:
Separate individual tool execution results from overall task completion.
A task cannot be marked successful unless its defined acceptance criteria have been verified with concrete evidence.
"""
import unittest
import tempfile
import shutil
import os
from unittest.mock import patch, MagicMock
from ghost.qa.contracts import TaskResult, ActionVerifier

class TestVerification(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_verify_file_write_empirical_proof(self):
        """File write verification requires non-zero disk size, SHA256 checksum, and expected content."""
        target_path = os.path.join(self.temp_dir, "test_output.html")

        # 1. Non-existent file
        ok, proof = ActionVerifier.verify_file_write(target_path)
        self.assertFalse(ok)
        self.assertFalse(proof["verified"])
        self.assertIn("does not exist", proof["error"])

        # 2. Zero-byte file
        with open(target_path, "w") as f:
            pass
        ok, proof = ActionVerifier.verify_file_write(target_path, min_bytes=10)
        self.assertFalse(ok)
        self.assertIn("below required minimum", proof["error"])

        # 3. File exists with content
        valid_html = "<!DOCTYPE html><html><body><h1>Apex Detailing</h1></body></html>"
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(valid_html)

        # Missing expected substring
        ok, proof = ActionVerifier.verify_file_write(target_path, min_bytes=10, expected_substring="NonExistentBrand")
        self.assertFalse(ok)
        self.assertIn("Expected substring", proof["error"])

        # Valid proof
        ok, proof = ActionVerifier.verify_file_write(target_path, min_bytes=10, expected_substring="Apex Detailing")
        self.assertTrue(ok)
        self.assertTrue(proof["verified"])
        self.assertEqual(proof["size_bytes"], len(valid_html.encode()))
        self.assertTrue(len(proof["sha256"]) == 64)
        self.assertIn("mtime", proof)

    @patch("urllib.request.urlopen")
    def test_verify_http_endpoint_empirical_proof(self, mock_urlopen):
        """HTTP verification checks status code, content length, and payload hashes."""
        # Mock successful 200 response
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"<html><title>Apex Detailing London</title></html>"
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        ok, proof = ActionVerifier.verify_http_endpoint(
            url="https://apex-detailing.vercel.app",
            expected_status=200,
            expected_substring="Apex Detailing London"
        )
        self.assertTrue(ok)
        self.assertTrue(proof["verified"])
        self.assertEqual(proof["status_code"], 200)
        self.assertTrue(proof["body_length"] > 0)
        self.assertTrue(len(proof["body_sha256"]) == 64)

        # Mock 404 response
        mock_resp.status = 404
        mock_resp.read.return_value = b"Not Found"
        ok, proof = ActionVerifier.verify_http_endpoint(
            url="https://apex-detailing.vercel.app/bad-link",
            expected_status=200
        )
        self.assertFalse(ok)
        self.assertFalse(proof["verified"])
        self.assertIn("Expected status 200, got 404", proof["error"])

    def test_evaluate_task_criteria_unverified_reporting(self):
        """A task cannot be marked completed without verified evidence; unverified tasks report remaining_work."""
        task_id = "task_build_luxury_demo"
        valid_path = os.path.join(self.temp_dir, "index.html")
        missing_path = os.path.join(self.temp_dir, "mobile_screenshot.jpg")

        with open(valid_path, "w", encoding="utf-8") as f:
            f.write("<html><body>Apex Luxury Demo</body></html>")

        criteria = [
            {
                "type": "file_exists",
                "description": "HTML sectional components assembled on disk",
                "path": valid_path,
                "min_bytes": 10,
                "expected_substring": "Apex Luxury Demo"
            },
            {
                "type": "file_exists",
                "description": "Mobile viewport screenshot captured on disk",
                "path": missing_path,
                "min_bytes": 1000
            }
        ]

        result: TaskResult = ActionVerifier.evaluate_task_criteria(task_id, criteria)

        # Status must be 'unverified' because screenshot was missing
        self.assertEqual(result.status, "unverified")
        self.assertFalse(result.verified)
        self.assertEqual(len(result.remaining_work), 1)
        self.assertEqual(result.remaining_work[0], "Mobile viewport screenshot captured on disk")
        self.assertEqual(len(result.errors), 1)
        self.assertIn("does not exist", result.errors[0])

        # Add the missing screenshot
        with open(missing_path, "wb") as f:
            f.write(b"PNG_MOCK_IMAGE_DATA_EXCEEDS_THOUSAND_BYTES" * 30)

        completed_result: TaskResult = ActionVerifier.evaluate_task_criteria(task_id, criteria)
        self.assertEqual(completed_result.status, "completed")
        self.assertTrue(completed_result.verified)
        self.assertEqual(len(completed_result.remaining_work), 0)
        self.assertEqual(len(completed_result.errors), 0)
        self.assertEqual(len(completed_result.evidence), 2)

if __name__ == "__main__":
    unittest.main()
