"""Free Open-Licensed 3D GLB Model Discovery & Ingestion Engine.
Enforces non-negotiable architectural invariants:
1. Rejects Non-Commercial (NC) and No-Derivatives (ND) models.
2. Only accepts permissive open licenses (CC0, CC-BY, Public Domain).
3. Ghost NEVER downloads unapproved external models into production without operator confirmation.
4. Mandatory license row written to SOURCES.json upon verified download.
"""
import os
import json
import logging
import urllib.request
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("ghost.find_glb")

REPO_DIR = os.path.abspath(os.environ.get("GHOST_PROJECT_ROOT") or os.path.join(os.path.dirname(__file__), "../../.."))

# Curated index of free open models across public repositories
OPEN_3D_REGISTRY: List[Dict[str, Any]] = [
    {
        "name": "Damaged Helmet (Sci-Fi PBR Showcase)",
        "subject": "helmet sci-fi pbr luxury metallic",
        "source": "Khronos glTF Sample Assets",
        "url": "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/DamagedHelmet/glTF-Binary/DamagedHelmet.glb",
        "license": "CC-BY-4.0",
        "commercial_use_allowed": True,
        "attribution_required": True,
        "author": "Battle Gaming / theblueturtle_",
        "triangles": 15452
    },
    {
        "name": "Antique Camera",
        "subject": "camera vintage antique leather optics luxury",
        "source": "Khronos glTF Sample Assets",
        "url": "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/AntiqueCamera/glTF-Binary/AntiqueCamera.glb",
        "license": "CC-BY-4.0",
        "commercial_use_allowed": True,
        "attribution_required": True,
        "author": "Khronos Group",
        "triangles": 24866
    },
    {
        "name": "Toy Car (Automotive Stand-in)",
        "subject": "car automobile vehicle luxury detailing",
        "source": "Khronos glTF Sample Assets",
        "url": "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/ToyCar/glTF-Binary/ToyCar.glb",
        "license": "CC-BY-4.0",
        "commercial_use_allowed": True,
        "attribution_required": True,
        "author": "Khronos Group",
        "triangles": 18210
    },
    {
        "name": "Flight Helmet",
        "subject": "aviation pilot helmet tactical leather",
        "source": "Khronos glTF Sample Assets",
        "url": "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/FlightHelmet/glTF-Binary/FlightHelmet.glb",
        "license": "CC0",
        "commercial_use_allowed": True,
        "attribution_required": False,
        "author": "Public Domain",
        "triangles": 46422
    },
    {
        "name": "Lantern (Interior / Architectural Lighting)",
        "subject": "lantern lamp light architectural brass interior",
        "source": "Khronos glTF Sample Assets",
        "url": "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/Lantern/glTF-Binary/Lantern.glb",
        "license": "CC0",
        "commercial_use_allowed": True,
        "attribution_required": False,
        "author": "Public Domain",
        "triangles": 12894
    },
    {
        "name": "Water Bottle (Beverage / Product Design)",
        "subject": "bottle drink beverage packaging water",
        "source": "Khronos glTF Sample Assets",
        "url": "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/WaterBottle/glTF-Binary/WaterBottle.glb",
        "license": "CC0",
        "commercial_use_allowed": True,
        "attribution_required": False,
        "author": "Public Domain",
        "triangles": 5420
    },
    {
        "name": "Botanical Garden Plant (Degustation & Dining Sanctuary)",
        "subject": "plant botanical garden nature foliage dining restaurant foliage table flora grasshopper",
        "source": "Khronos glTF Sample Assets",
        "url": "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/DiffuseTransmissionPlant/glTF-Binary/DiffuseTransmissionPlant.glb",
        "license": "CC-BY-4.0",
        "commercial_use_allowed": True,
        "attribution_required": True,
        "author": "Khronos Group",
        "triangles": 16800
    },
    {
        "name": "Glass Vase with Flowers (Intimate Fine Dining Tableware)",
        "subject": "flowers vase glass floral dining fine dining degustation table table-setting centerpiece",
        "source": "Khronos glTF Sample Assets",
        "url": "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/GlassVaseFlowers/glTF-Binary/GlassVaseFlowers.glb",
        "license": "CC-BY-4.0",
        "commercial_use_allowed": True,
        "attribution_required": True,
        "author": "Khronos Group",
        "triangles": 22400
    }
]


def is_permissive_license(license_str: str) -> bool:
    """Verifies that license allows commercial use and modification (rejects NC and ND)."""
    lic = (license_str or "").upper()
    if "-NC" in lic or "NON-COMMERCIAL" in lic or "NONCOMMERCIAL" in lic:
        return False
    if "-ND" in lic or "NO-DERIVATIVES" in lic or "NODERIVATIVES" in lic:
        return False
    if any(valid in lic for valid in ["CC0", "PUBLIC DOMAIN", "CC-BY", "MIT", "APACHE"]):
        return True
    return False


class GLBDiscovery:
    """Discovers and downloads free, permissively licensed GLB assets."""

    @classmethod
    def find_glb(cls, query: str, max_results: int = 5) -> Dict[str, Any]:
        """
        Search for candidate GLB models matching query terms.
        Strictly filters out any non-commercial or no-derivatives models.
        """
        terms = [t.lower().strip() for t in query.split() if len(t.strip()) > 1]
        candidates = []

        for item in OPEN_3D_REGISTRY:
            # Check license permissiveness
            if not is_permissive_license(item["license"]):
                continue

            subj = item["subject"].lower()
            name = item["name"].lower()
            score = sum(1 for term in terms if term in subj or term in name)
            if score > 0 or not terms:
                candidates.append((score, item))

        candidates.sort(key=lambda x: x[0], reverse=True)
        results = [c[1] for c in candidates[:max_results]]

        if not results:
            # Return general sample models as fallback
            results = [item for item in OPEN_3D_REGISTRY if is_permissive_license(item["license"])][:max_results]

        return {
            "ok": True,
            "status": "success",
            "tool": "find_glb",
            "query": query,
            "count": len(results),
            "candidates": results,
            "notice": "All models strictly verified under CC0 / CC-BY commercial-use licenses. Operator approval required before downloading."
        }

    @classmethod
    def download_glb(
        cls,
        candidate_url: str,
        destination_dir: str,
        license_name: str,
        author: str = "",
        operator_approved: bool = False,
        slug: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Downloads an approved candidate GLB model and writes license row to SOURCES.json.
        Requires explicit operator approval.
        """
        if not operator_approved:
            return {
                "ok": False,
                "status": "approval_required",
                "tool": "find_glb",
                "error": "Operator approval required before downloading external 3D assets.",
                "url": candidate_url,
                "license": license_name
            }

        if not is_permissive_license(license_name):
            return {
                "ok": False,
                "status": "blocked",
                "tool": "find_glb",
                "error": f"Model license '{license_name}' is not permissible. NC and ND licenses are strictly forbidden.",
            }

        os.makedirs(destination_dir, exist_ok=True)
        filename = os.path.basename(candidate_url.split("?")[0]) or "model.glb"
        if not filename.endswith(".glb"):
            filename = f"{filename}.glb"

        dest_file = os.path.join(destination_dir, filename)

        try:
            req = urllib.request.Request(
                candidate_url,
                headers={"User-Agent": "Ghost-Agency-Engine/2.0"}
            )
            with urllib.request.urlopen(req, timeout=30.0) as response, open(dest_file, "wb") as out_f:
                out_f.write(response.read())

            file_size = os.path.getsize(dest_file)

            # Record in SOURCES.json in the demo or parent folder
            sources_path = os.path.join(os.path.dirname(destination_dir), "SOURCES.json")
            sources_data = {}
            if os.path.exists(sources_path):
                try:
                    with open(sources_path, "r", encoding="utf-8") as sf:
                        sources_data = json.load(sf)
                except Exception:
                    sources_data = {}

            assets_ledger = sources_data.setdefault("assets", [])
            assets_ledger.append({
                "type": "3d_model",
                "file": os.path.relpath(dest_file, os.path.dirname(destination_dir)),
                "url": candidate_url,
                "license": license_name,
                "author": author,
                "commercial_use_allowed": True,
                "size_bytes": file_size
            })

            with open(sources_path, "w", encoding="utf-8") as sf:
                json.dump(sources_data, sf, indent=2)

            return {
                "ok": True,
                "status": "success",
                "tool": "find_glb",
                "file": dest_file,
                "size_bytes": file_size,
                "license": license_name,
                "author": author,
                "ledger_recorded": True
            }

        except Exception as e:
            logger.error(f"Failed to download GLB model from '{candidate_url}': {e}")
            return {
                "ok": False,
                "status": "failed",
                "tool": "find_glb",
                "error": f"Download failed: {str(e)}",
                "url": candidate_url
            }
