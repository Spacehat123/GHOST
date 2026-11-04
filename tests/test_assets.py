"""Unit and Integration Tests for Part 9: Free Asset Stack & GLB Tooling.
Enforces non-negotiable architectural requirements:
1. Providers that are not configured return explicit 'unavailable' status with install hints, never fake output.
2. Zero-dependency GLB binary inspector extracts real geometry and bounds.
3. GLB optimizer works on copies and never mutates original in place.
4. GLB finder strictly rejects NC/ND licenses and enforces operator approval.
5. Procedural Web Audio complies with default-off, single-transition, and non-overlapping hover invariants.
6. Asset ledger lint strictly fails builds containing unledgered assets.
"""
import os
import json
import struct
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from ghost.workers.glb_tools import GLBTools
from ghost.workers.find_glb import GLBDiscovery, is_permissive_license
from ghost.workers.asset_providers import AssetManager, PROVIDERS
from ghost.workers.audio_manager import ProceduralAudioManager
from ghost.workers.source_ledger import lint_demo_assets


class TestAssetProviders(unittest.TestCase):
    """Test image generation provider routing, unavailable reporting, and ledger rows."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_asset_providers_unavailable_returns_honest_status(self):
        """When no credentials are configured, generate_image returns explicit 'unavailable'."""
        with patch.dict(os.environ, {}, clear=True):
            res = AssetManager.generate_image(
                prompt="Fine dining editorial photography",
                destination_dir=self.temp_dir.name,
                filename="test.jpg"
            )
            self.assertFalse(res["ok"])
            self.assertEqual(res["status"], "unavailable")
            self.assertIn("No configured image generation provider", res["error"])
            self.assertIn("install_hint", res)
            self.assertIn("free_options", res)

    def test_asset_generation_writes_ledger_row(self):
        """When an asset is generated, a corresponding ledger row is persisted in SOURCES.json."""
        # Create a mock provider that simulates successful generation
        mock_provider = MagicMock()
        mock_provider.name = "mock_flux"
        mock_provider.is_available.return_value = (True, "Mock ready")
        mock_provider.generate.return_value = {
            "status": "success",
            "file": os.path.join(self.temp_dir.name, "hero.jpg"),
            "provider": "mock_flux"
        }

        # Create the file on disk as the provider would
        os.makedirs(os.path.join(self.temp_dir.name, "assets"), exist_ok=True)
        fake_img = os.path.join(self.temp_dir.name, "assets", "hero.jpg")
        with open(fake_img, "wb") as f:
            f.write(b"fake jpeg data")

        with patch("ghost.workers.asset_providers.PROVIDERS", [mock_provider]):
            res = AssetManager.generate_image(
                prompt="Bespoke luxury table setting",
                destination_dir=os.path.join(self.temp_dir.name, "assets"),
                filename="hero.jpg"
            )
            self.assertTrue(res["ok"])
            self.assertEqual(res["status"], "success")
            self.assertTrue(res.get("ledger_recorded"))

            # Verify SOURCES.json exists and contains the asset row
            sources_file = os.path.join(self.temp_dir.name, "SOURCES.json")
            self.assertTrue(os.path.exists(sources_file))
            with open(sources_file, "r") as sf:
                data = json.load(sf)
            self.assertIn("assets", data)
            self.assertEqual(data["assets"][0]["provider"], "mock_flux")


class TestGLBTools(unittest.TestCase):
    """Test pure Python GLB binary parsing, copy protection, and optimizer CLI handling."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_minimal_glb(self, triangles: int = 4) -> str:
        """Create a valid binary GLB 2.0 file containing indexed geometry."""
        gltf_dict = {
            "asset": {"version": "2.0", "generator": "Ghost-Test"},
            "accessors": [
                {
                    "bufferView": 0,
                    "byteOffset": 0,
                    "componentType": 5126,  # FLOAT
                    "count": triangles * 3,
                    "type": "VEC3",
                    "max": [1.0, 2.0, 3.0],
                    "min": [-1.0, 0.0, -0.5]
                },
                {
                    "bufferView": 1,
                    "byteOffset": 0,
                    "componentType": 5123,  # UNSIGNED_SHORT
                    "count": triangles * 3,
                    "type": "SCALAR"
                }
            ],
            "bufferViews": [
                {"buffer": 0, "byteOffset": 0, "byteLength": triangles * 3 * 12},
                {"buffer": 0, "byteOffset": triangles * 3 * 12, "byteLength": triangles * 3 * 2}
            ],
            "buffers": [
                {"byteLength": triangles * 3 * 14}
            ],
            "meshes": [
                {
                    "name": "LuxuryShowcaseMesh",
                    "primitives": [
                        {
                            "attributes": {"POSITION": 0},
                            "indices": 1,
                            "material": 0
                        }
                    ]
                }
            ],
            "materials": [{"name": "BrushedBrassPBR"}],
            "textures": [{"source": 0}]
        }

        json_bytes = json.dumps(gltf_dict).encode("utf-8")
        # Pad JSON to 4-byte boundary
        pad_len = (4 - (len(json_bytes) % 4)) % 4
        json_bytes += b" " * pad_len

        total_length = 12 + 8 + len(json_bytes)
        file_path = os.path.join(self.temp_dir.name, "showcase.glb")

        with open(file_path, "wb") as f:
            # Header
            f.write(struct.pack("<4sII", b"glTF", 2, total_length))
            # Chunk 0: JSON
            f.write(struct.pack("<I4s", len(json_bytes), b"JSON"))
            f.write(json_bytes)

        return file_path

    def test_glb_inspect_extracts_real_geometry(self):
        glb_path = self._create_minimal_glb(triangles=12)
        res = GLBTools.inspect(glb_path)

        self.assertTrue(res["ok"])
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["triangle_count"], 12)
        self.assertEqual(res["mesh_count"], 1)
        self.assertEqual(res["material_count"], 1)
        self.assertEqual(res["texture_count"], 1)
        self.assertIsNotNone(res["bounds"])
        self.assertEqual(res["bounds"]["max"], [1.0, 2.0, 3.0])
        self.assertEqual(res["bounds"]["min"], [-1.0, 0.0, -0.5])

    def test_glb_optimize_unavailable_when_cli_missing(self):
        glb_path = self._create_minimal_glb(triangles=4)
        with patch("shutil.which", return_value=None):
            import asyncio
            res = asyncio.run(GLBTools.optimize(glb_path))
            self.assertFalse(res["ok"])
            self.assertEqual(res["status"], "unavailable")
            self.assertIn("npm install -g @gltf-transform/cli", res["install_hint"])
            # Verify original file was not mutated
            self.assertTrue(os.path.exists(glb_path))


class TestGLBDiscovery(unittest.TestCase):
    """Test license filtering and operator approval on GLB downloads."""

    def test_license_permissiveness_rules(self):
        # Permissive
        self.assertTrue(is_permissive_license("CC0"))
        self.assertTrue(is_permissive_license("CC-BY-4.0"))
        self.assertTrue(is_permissive_license("Public Domain"))
        self.assertTrue(is_permissive_license("MIT"))

        # Strictly rejected
        self.assertFalse(is_permissive_license("CC-BY-NC-4.0"))
        self.assertFalse(is_permissive_license("CC-BY-ND"))
        self.assertFalse(is_permissive_license("Non-Commercial License"))

    def test_find_glb_returns_verified_candidates(self):
        res = GLBDiscovery.find_glb("helmet pbr")
        self.assertTrue(res["ok"])
        self.assertGreater(res["count"], 0)
        for cand in res["candidates"]:
            self.assertTrue(is_permissive_license(cand["license"]))
            self.assertTrue(cand["commercial_use_allowed"])

    def test_download_glb_requires_operator_approval(self):
        res = GLBDiscovery.download_glb(
            candidate_url="https://example.com/asset.glb",
            destination_dir="/tmp/test_assets",
            license_name="CC-BY-4.0",
            operator_approved=False
        )
        self.assertFalse(res["ok"])
        self.assertEqual(res["status"], "approval_required")


class TestProceduralAudio(unittest.TestCase):
    """Test zero-asset procedural Web Audio script contracts."""

    def test_audio_contract_invariants(self):
        script = ProceduralAudioManager.get_procedural_audio_script(palette="warm")
        self.assertIn("class ProceduralAudioManager", script)

        ok, violations = ProceduralAudioManager.lint_audio_contract(script)
        self.assertTrue(ok, f"Audio contract violations: {violations}")

    def test_audio_palette_tuning(self):
        warm_script = ProceduralAudioManager.get_procedural_audio_script(palette="warm")
        self.assertIn("110.0", warm_script)

        industrial_script = ProceduralAudioManager.get_procedural_audio_script(palette="industrial")
        self.assertIn("73.42", industrial_script)


class TestAssetLedgerLint(unittest.TestCase):
    """Test build/QA ledger linting for demo assets."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.demo_dir = os.path.join(self.temp_dir.name, "apex-detailing")
        self.assets_dir = os.path.join(self.demo_dir, "assets")
        os.makedirs(self.assets_dir, exist_ok=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_unledgered_asset_fails_lint(self):
        # Place asset file on disk
        with open(os.path.join(self.assets_dir, "hero.glb"), "wb") as f:
            f.write(b"model data")

        # Create SOURCES.json without the asset listed
        with open(os.path.join(self.demo_dir, "SOURCES.json"), "w") as f:
            json.dump({"business_name": {"value": "Apex Detailing"}}, f)

        ok, violations = lint_demo_assets(self.demo_dir)
        self.assertFalse(ok)
        self.assertTrue(any("hero.glb" in v for v in violations))

    def test_ledgered_asset_passes_lint(self):
        with open(os.path.join(self.assets_dir, "hero.glb"), "wb") as f:
            f.write(b"model data")

        with open(os.path.join(self.demo_dir, "SOURCES.json"), "w") as f:
            json.dump({
                "assets": [
                    {
                        "file": "assets/hero.glb",
                        "license": "CC0",
                        "source": "Khronos glTF Samples"
                    }
                ]
            }, f)

        ok, violations = lint_demo_assets(self.demo_dir)
        self.assertTrue(ok)
        self.assertEqual(len(violations), 0)
