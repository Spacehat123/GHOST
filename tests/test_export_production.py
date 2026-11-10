"""Tests for the Next.js 14 Production Exporter (Fix 4).

Validates that static Ghost demos are compiled into modern Next.js 14 App Router
codebases with serverless API route handlers, Tailwind design tokens, and honeypot protection.
"""
import os
import json
import shutil
import tempfile
import unittest
import uuid

from ghost.workers.export_demo_to_production import export_demo_to_production


class TestExportDemoToProduction(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ghost-test-export-")
        self.demos_dir = os.path.join(self.tmp, "demos")
        self.out_base = os.path.join(self.tmp, "dist", "production")
        os.makedirs(self.demos_dir, exist_ok=True)
        os.makedirs(self.out_base, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _create_mock_demo(self, slug: str) -> str:
        demo_dir = os.path.join(self.demos_dir, slug)
        os.makedirs(demo_dir, exist_ok=True)
        assets_dir = os.path.join(demo_dir, "assets", "photos")
        os.makedirs(assets_dir, exist_ok=True)

        # Mock photo
        with open(os.path.join(assets_dir, "photo-01.jpg"), "wb") as f:
            f.write(b"\xff\xd8\xff\xe0mock_photo_data")

        # Mock BRAND.json
        brand = {
            "business_name": "Apex Performance Studio",
            "slug": slug,
            "domain": "gyms_fitness",
            "location": "Mayfair, London",
            "tagline": "Engineered human performance in Mayfair.",
            "phone": "+44 20 7946 0999",
            "design_tokens": {
                "bg": "#0d0f12",
                "surface": "#161a1f",
                "text": "#f0f2f5",
                "muted": "#8a94a6",
                "accent": "#ff4d4d",
                "accent_secondary": "#cc3333",
                "border": "rgba(255, 255, 255, 0.08)",
                "font_head": "Cinzel, serif",
                "font_body": "Plus Jakarta Sans, sans-serif"
            },
            "services": [
                {"title": "Elite Assessment", "category": "Diagnostics", "desc": "VO2 max and metabolic profiling."}
            ],
            "reviews": [
                {"quote": "Unrivalled technical coaching.", "author": "Lord Sterling", "source": "Google Review"}
            ],
            "assets": {
                "hero_bg": "assets/photos/photo-01.jpg",
                "bento_img1": "assets/photos/photo-01.jpg",
                "gallery_1": "assets/photos/photo-01.jpg"
            }
        }
        with open(os.path.join(demo_dir, "BRAND.json"), "w", encoding="utf-8") as f:
            json.dump(brand, f, indent=2)

        with open(os.path.join(demo_dir, "index.html"), "w", encoding="utf-8") as f:
            f.write("<html><body>Apex Performance Studio</body></html>")

        return demo_dir

    def test_export_compiles_nextjs_codebase_successfully(self):
        slug = f"apex-{uuid.uuid4().hex[:6]}"
        self._create_mock_demo(slug)

        result = export_demo_to_production(
            slug=slug,
            demos_dir=self.demos_dir,
            output_base_dir=self.out_base
        )

        self.assertEqual(result["status"], "exported")
        self.assertEqual(result["slug"], slug)
        self.assertEqual(result["framework"], "Next.js 14 App Router")
        self.assertGreaterEqual(result["files_created"], 10)

        target_dir = result["target_dir"]

        # Check required files
        self.assertTrue(os.path.isfile(os.path.join(target_dir, "package.json")))
        self.assertTrue(os.path.isfile(os.path.join(target_dir, "tsconfig.json")))
        self.assertTrue(os.path.isfile(os.path.join(target_dir, "tailwind.config.ts")))
        self.assertTrue(os.path.isfile(os.path.join(target_dir, "postcss.config.js")))
        self.assertTrue(os.path.isfile(os.path.join(target_dir, "app", "globals.css")))
        self.assertTrue(os.path.isfile(os.path.join(target_dir, "app", "layout.tsx")))
        self.assertTrue(os.path.isfile(os.path.join(target_dir, "app", "page.tsx")))
        self.assertTrue(os.path.isfile(os.path.join(target_dir, "app", "api", "contact", "route.ts")))
        self.assertTrue(os.path.isfile(os.path.join(target_dir, "app", "api", "book", "route.ts")))
        self.assertTrue(os.path.isfile(os.path.join(target_dir, "README.md")))
        self.assertTrue(os.path.isfile(os.path.join(target_dir, "public", "assets", "photos", "photo-01.jpg")))

        # Check package.json contents
        with open(os.path.join(target_dir, "package.json"), "r") as f:
            pkg = json.load(f)
        self.assertIn("next", pkg["dependencies"])
        self.assertIn("react", pkg["dependencies"])
        self.assertIn("tailwindcss", pkg["devDependencies"])

        # Check API routes honeypot protection
        with open(os.path.join(target_dir, "app", "api", "contact", "route.ts"), "r") as f:
            contact_code = f.read()
        self.assertIn("company_website", contact_code)
        self.assertIn("GHOST_CRM_WEBHOOK_URL", contact_code)

        # Check design tokens in globals.css
        with open(os.path.join(target_dir, "app", "globals.css"), "r") as f:
            css = f.read()
        self.assertIn("--accent-color: #ff4d4d", css)
        self.assertIn("--bg-color: #0d0f12", css)

    def test_export_raises_for_missing_demo(self):
        with self.assertRaises(FileNotFoundError):
            export_demo_to_production(
                slug="non-existent-studio-404",
                demos_dir=self.demos_dir,
                output_base_dir=self.out_base
            )


if __name__ == "__main__":
    unittest.main()
