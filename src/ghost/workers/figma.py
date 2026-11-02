"""Figma REST API Bridge & Design Token Extraction Engine.

Wraps the Figma REST API (https://api.figma.com/v1/) to enable:
- File metadata inspection & node retrieval
- Image rendering of specific frames/components
- Non-destructive design token extraction into Tailwind CSS variables
- Posting review notes and annotations

HARD RULE ENFORCED:
Figma is strictly for POST-CONTRACT production website building ($1,000+ closed clients).
Never use Figma or attempt to parse Figma files for pre-contract cold pitch demos.
Cold pitch demos strictly use the OpenCode state machine.
"""
import os
import json
import logging
import urllib.request
import urllib.error
import urllib.parse
from typing import Dict, Any, List, Optional

logger = logging.getLogger("ghost.workers.figma")

CONFIGS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../configs"))
FIGMA_CONFIG_PATH = os.path.join(CONFIGS_DIR, "figma.json")
FIGMA_API_BASE = "https://api.figma.com/v1"


class FigmaBridge:
    """Wraps Figma REST API for post-contract design inspection and token extraction."""

    @classmethod
    def get_token(cls) -> Optional[str]:
        """Load Figma Personal Access Token from config or environment."""
        if os.environ.get("FIGMA_ACCESS_TOKEN"):
            return os.environ.get("FIGMA_ACCESS_TOKEN")
        if os.environ.get("FIGMA_TOKEN"):
            return os.environ.get("FIGMA_TOKEN")
        if os.path.exists(FIGMA_CONFIG_PATH):
            try:
                with open(FIGMA_CONFIG_PATH, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    tok = cfg.get("access_token") or cfg.get("token")
                    if tok and tok.strip():
                        return tok.strip()
            except Exception as e:
                logger.warning(f"Failed to read {FIGMA_CONFIG_PATH}: {e}")
        return None

    @classmethod
    def _make_request(
        cls,
        endpoint: str,
        method: str = "GET",
        data: Optional[Dict[str, Any]] = None,
        timeout: int = 15
    ) -> Dict[str, Any]:
        """Execute an authenticated request against Figma API."""
        token = cls.get_token()
        if not token:
            return {
                "status": "error",
                "error": "No Figma access token found. Please set 'access_token' in configs/figma.json."
            }

        url = f"{FIGMA_API_BASE}{endpoint}" if endpoint.startswith("/") else f"{FIGMA_API_BASE}/{endpoint}"
        headers = {
            "X-Figma-Token": token,
            "User-Agent": "Ghost-TF141-Bridge/1.0"
        }

        body_bytes = None
        if data is not None:
            headers["Content-Type"] = "application/json"
            body_bytes = json.dumps(data).encode("utf-8")

        req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                res_body = response.read().decode("utf-8")
                parsed = json.loads(res_body)
                return {"status": "success", "data": parsed}
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            logger.error(f"Figma API HTTP {e.code}: {err_body}")
            return {
                "status": "error",
                "http_code": e.code,
                "error": f"Figma API error {e.code}: {err_body}"
            }
        except Exception as e:
            logger.error(f"Figma API request failed: {e}")
            return {"status": "error", "error": str(e)}

    @classmethod
    def get_file(cls, file_key: str, depth: Optional[int] = None) -> Dict[str, Any]:
        """Fetch entire Figma file document tree (/v1/files/{file_key})."""
        file_key = file_key.strip()
        endpoint = f"/files/{file_key}"
        if depth:
            endpoint += f"?depth={depth}"
        return cls._make_request(endpoint, method="GET")

    @classmethod
    def get_file_nodes(cls, file_key: str, ids: List[str]) -> Dict[str, Any]:
        """Fetch specific node trees from a Figma file (/v1/files/{file_key}/nodes?ids=...)."""
        file_key = file_key.strip()
        ids_param = urllib.parse.quote(",".join(ids))
        endpoint = f"/files/{file_key}/nodes?ids={ids_param}"
        return cls._make_request(endpoint, method="GET")

    @classmethod
    def get_image_renders(
        cls,
        file_key: str,
        ids: List[str],
        format: str = "png",
        scale: float = 2.0
    ) -> Dict[str, Any]:
        """Render specific nodes/frames as images (/v1/images/{file_key}?ids=...)."""
        file_key = file_key.strip()
        ids_param = urllib.parse.quote(",".join(ids))
        endpoint = f"/images/{file_key}?ids={ids_param}&format={format}&scale={scale}"
        return cls._make_request(endpoint, method="GET")

    @classmethod
    def post_comment(
        cls,
        file_key: str,
        message: str,
        client_meta: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Post a review note or comment on a Figma canvas (/v1/files/{file_key}/comments)."""
        file_key = file_key.strip()
        payload: Dict[str, Any] = {"message": message}
        if client_meta:
            payload["client_meta"] = client_meta
        return cls._make_request(f"/files/{file_key}/comments", method="POST", data=payload)

    @classmethod
    def extract_design_tokens(cls, file_key: str) -> Dict[str, Any]:
        """
        Parses color styles, typography text styles, components, and layout dimensions
        from the Figma document tree and converts them into Tailwind CSS variables.
        """
        file_res = cls.get_file(file_key, depth=3)
        if file_res.get("status") != "success":
            return file_res

        doc = file_res.get("data", {})
        document_tree = doc.get("document", {})
        styles_meta = doc.get("styles", {})

        tokens: Dict[str, Any] = {
            "file_name": doc.get("name", "Untitled"),
            "last_modified": doc.get("lastModified"),
            "colors": {},
            "typography": {},
            "components": {},
            "tailwind_theme_extend": {
                "colors": {},
                "fontFamily": {},
                "borderRadius": {}
            }
        }

        # Recursive token crawler
        def _rgb_to_hex(r: float, g: float, b: float) -> str:
            return f"#{int(round(r * 255)):02x}{int(round(g * 255)):02x}{int(round(b * 255)):02x}"

        def _traverse(node: Dict[str, Any]):
            n_name = node.get("name", "").strip()
            n_type = node.get("type", "")

            # Colors from fills
            if "fills" in node and isinstance(node["fills"], list):
                for fill in node["fills"]:
                    if fill.get("type") == "SOLID" and fill.get("visible", True):
                        c = fill.get("color", {})
                        if "r" in c and "g" in c and "b" in c:
                            hex_val = _rgb_to_hex(c["r"], c["g"], c["b"])
                            clean_name = n_name.lower().replace(" ", "-").replace("/", "-")
                            if clean_name and clean_name not in tokens["colors"]:
                                tokens["colors"][clean_name] = {
                                    "hex": hex_val,
                                    "opacity": fill.get("opacity", 1.0)
                                }
                                tokens["tailwind_theme_extend"]["colors"][clean_name] = hex_val

            # Typography styles
            if n_type == "TEXT" and "style" in node:
                st = node["style"]
                font_fam = st.get("fontFamily", "Inter")
                font_size = st.get("fontSize", 16)
                font_weight = st.get("fontWeight", 400)
                line_height_px = st.get("lineHeightPx", 24)

                type_key = n_name.lower().replace(" ", "-").replace("/", "-") or f"font-{int(font_size)}"
                if type_key not in tokens["typography"]:
                    tokens["typography"][type_key] = {
                        "fontFamily": font_fam,
                        "fontSize": f"{font_size}px",
                        "fontWeight": font_weight,
                        "lineHeight": f"{line_height_px}px"
                    }
                    if font_fam not in tokens["tailwind_theme_extend"]["fontFamily"].values():
                        fam_slug = font_fam.lower().replace(" ", "-")
                        tokens["tailwind_theme_extend"]["fontFamily"][fam_slug] = [font_fam, "sans-serif"]

            # Component mapping
            if n_type in ["COMPONENT", "COMPONENT_SET", "INSTANCE"]:
                c_id = node.get("id")
                tokens["components"][n_name] = {
                    "id": c_id,
                    "type": n_type,
                    "width": node.get("absoluteBoundingBox", {}).get("width"),
                    "height": node.get("absoluteBoundingBox", {}).get("height")
                }

            # Corner Radii
            if "cornerRadius" in node:
                r_val = node["cornerRadius"]
                r_slug = f"radius-{int(r_val)}"
                tokens["tailwind_theme_extend"]["borderRadius"][r_slug] = f"{r_val}px"

            for child in node.get("children", []):
                _traverse(child)

        _traverse(document_tree)
        return {
            "status": "success",
            "file_key": file_key,
            "tokens": tokens
        }
