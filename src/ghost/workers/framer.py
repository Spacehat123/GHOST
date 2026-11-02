"""Framer REST & Component Synthesis Engine for Ghost.

Enables automated CMS collection sync and Framer-ready React code component generation
for closed $1,000+ client accounts.
Reads API key and project settings from `configs/framer.json`.
"""
import os
import json
import logging
import urllib.request
import urllib.error
import urllib.parse
from typing import Dict, Any, List, Optional

logger = logging.getLogger("ghost.workers.framer")

CONFIGS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../configs"))
FRAMER_CONFIG_PATH = os.path.join(CONFIGS_DIR, "framer.json")
FRAMER_API_BASE = "https://api.framer.com/v1"


class FramerBridge:
    """Wraps Framer API and synthesizes Framer motion code components."""

    @classmethod
    def get_config(cls) -> Dict[str, str]:
        """Load Framer configuration from `configs/framer.json` or environment."""
        cfg = {"api_key": "", "project_id": ""}
        if os.environ.get("FRAMER_API_KEY"):
            cfg["api_key"] = os.environ.get("FRAMER_API_KEY", "")
        if os.environ.get("FRAMER_PROJECT_ID"):
            cfg["project_id"] = os.environ.get("FRAMER_PROJECT_ID", "")

        if os.path.exists(FRAMER_CONFIG_PATH):
            try:
                with open(FRAMER_CONFIG_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if not cfg["api_key"] and data.get("api_key"):
                        cfg["api_key"] = data["api_key"].strip()
                    if not cfg["project_id"] and data.get("project_id"):
                        cfg["project_id"] = data["project_id"].strip()
            except Exception as e:
                logger.warning(f"Failed to read {FRAMER_CONFIG_PATH}: {e}")
        return cfg

    @classmethod
    def get_client_headers(cls) -> Dict[str, str]:
        """Headers with Bearer authorization for Framer API."""
        cfg = cls.get_config()
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Ghost-TF141-FramerBridge/1.0"
        }
        if cfg["api_key"]:
            headers["Authorization"] = f"Bearer {cfg['api_key']}"
        return headers

    @classmethod
    def sync_cms_items(
        cls,
        collection_id: str,
        items: List[Dict[str, Any]],
        project_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Pushes scraped menus, reviews, or project deliverables to a Framer CMS collection.
        If live API access is restricted by token scope, safely structures the items
        into Framer CMS payload format and returns the staged sync payload.
        """
        cfg = cls.get_config()
        p_id = project_id or cfg["project_id"] or "default-project"
        headers = cls.get_client_headers()

        if not cfg["api_key"]:
            return {
                "status": "error",
                "error": "No Framer API key configured in configs/framer.json or FRAMER_API_KEY env."
            }

        endpoint = f"{FRAMER_API_BASE}/projects/{p_id}/collections/{collection_id}/items"
        payload = {"items": items}

        try:
            req = urllib.request.Request(
                endpoint,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=12) as response:
                res_body = response.read().decode("utf-8")
                return {
                    "status": "success",
                    "collection_id": collection_id,
                    "items_synced": len(items),
                    "response": json.loads(res_body)
                }
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            logger.warning(f"Framer API returned HTTP {e.code}: {err_body}")
            # Non-blocking fallback: Stage payload for manual or CLI CMS import
            return {
                "status": "staged",
                "collection_id": collection_id,
                "project_id": p_id,
                "items_count": len(items),
                "http_status": e.code,
                "message": f"Framer API returned HTTP {e.code}. Items staged in Framer CMS JSON schema.",
                "payload": payload
            }
        except Exception as e:
            logger.warning(f"Framer CMS sync exception: {e}")
            return {
                "status": "staged",
                "collection_id": collection_id,
                "project_id": p_id,
                "items_count": len(items),
                "message": f"Staged items locally: {e}",
                "payload": payload
            }

    @classmethod
    def generate_framer_component(
        cls,
        name: str,
        style_tokens: Optional[Dict[str, Any]] = None,
        motion_spring: bool = True
    ) -> Dict[str, Any]:
        """
        Generates production-grade Framer-ready React code component using Motion physics.
        Includes property controls (addPropertyControls from "framer").
        """
        comp_name = "".join([w.capitalize() for w in name.replace("-", " ").replace("_", " ").split()])
        tokens = style_tokens or {
            "bg": "#fcfbf9",
            "surface": "#f5f2eb",
            "accent": "#997328",
            "text": "#1a1816",
            "border": "#e5ded3"
        }

        spring_props = (
            "transition={{ type: 'spring', stiffness: 380, damping: 28 }}"
            if motion_spring
            else "transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}"
        )

        code = f'''import * as React from "react"
import {{ motion }} from "framer-motion"
import {{ addPropertyControls, ControlType }} from "framer"

/**
 * @framerSupportedLayoutWidth auto
 * @framerSupportedLayoutHeight auto
 */
export default function {comp_name}(props) {{
    const {{
        title = "Artisanal Curations",
        subtitle = "Sourced with Classical Discipline",
        accentColor = "{tokens.get('accent', '#997328')}",
        backgroundColor = "{tokens.get('surface', '#f5f2eb')}",
        textColor = "{tokens.get('text', '#1a1816')}",
        onClick,
    }} = props

    return (
        <motion.div
            initial={{{{ opacity: 0, y: 16 }}}}
            animate={{{{ opacity: 1, y: 0 }}}}
            whileHover={{{{ y: -4, scale: 1.01 }}}}
            whileTap={{{{ scale: 0.98 }}}}
            {spring_props}
            onClick={{onClick}}
            style={{{{
                backgroundColor,
                color: textColor,
                borderRadius: 24,
                padding: "32px 36px",
                border: "1px solid {tokens.get('border', '#e5ded3')}",
                boxShadow: "0 20px 40px -15px rgba(0, 0, 0, 0.05)",
                backdropFilter: "blur(24px)",
                WebkitBackdropFilter: "blur(24px)",
                display: "flex",
                flexDirection: "column",
                gap: 12,
                cursor: "pointer",
                maxWidth: 480,
                fontFamily: "Inter, sans-serif",
            }}}}
        >
            <span
                style={{{{
                    fontSize: 11,
                    textTransform: "uppercase",
                    letterSpacing: "0.2em",
                    fontFamily: "monospace",
                    color: accentColor,
                    fontWeight: 600,
                }}}}
            >
                {{subtitle}}
            </span>
            <h3
                style={{{{
                    margin: 0,
                    fontSize: 24,
                    fontWeight: 700,
                    letterSpacing: "-0.02em",
                    color: textColor,
                }}}}
            >
                {{title}}
            </h3>
        </motion.div>
    )
}}

addPropertyControls({comp_name}, {{
    title: {{
        type: ControlType.String,
        title: "Title",
        defaultValue: "Artisanal Curations",
    }},
    subtitle: {{
        type: ControlType.String,
        title: "Subtitle",
        defaultValue: "Sourced with Classical Discipline",
    }},
    accentColor: {{
        type: ControlType.Color,
        title: "Accent",
        defaultValue: "{tokens.get('accent', '#997328')}",
    }},
    backgroundColor: {{
        type: ControlType.Color,
        title: "Background",
        defaultValue: "{tokens.get('surface', '#f5f2eb')}",
    }},
    textColor: {{
        type: ControlType.Color,
        title: "Text",
        defaultValue: "{tokens.get('text', '#1a1816')}",
    }},
}})
'''
        return {
            "status": "success",
            "component_name": comp_name,
            "motion_spring": motion_spring,
            "code": code
        }
