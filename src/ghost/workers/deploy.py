"""Vercel Deployment Helper for Ghost Demos & Production Delivery.

Allows zero-configuration deployment of prospect demo packages or production client sites
via `bunx vercel`. Gated by VERCEL_TOKEN from `configs/vercel.json` (or environment).
Uses neutral project naming and requires Tactical Authorization before cloud dispatch.
"""
import asyncio
import os
import json
import logging
import shutil
from typing import Dict, Any, Optional
from ghost.mcp.bridge import ShellExecutor, request_tactical_authorization

logger = logging.getLogger("ghost.workers.deploy")

CONFIGS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../configs"))
VERCEL_CONFIG_PATH = os.path.join(CONFIGS_DIR, "vercel.json")
DEFAULT_DEMOS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../demos"))
DEMOS_DIR = os.environ.get("GHOST_DEMOS_DIR") or DEFAULT_DEMOS_DIR


class VercelDeployer:
    """Manages deployments of static demos and projects to Vercel."""

    @classmethod
    def get_token(cls) -> Optional[str]:
        """Load Vercel Personal Access Token from config or environment."""
        if os.environ.get("VERCEL_TOKEN"):
            return os.environ.get("VERCEL_TOKEN")
        if os.path.exists(VERCEL_CONFIG_PATH):
            try:
                with open(VERCEL_CONFIG_PATH, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    tok = cfg.get("vercel_token") or cfg.get("token")
                    if tok and tok.strip():
                        return tok.strip()
            except Exception as e:
                logger.warning(f"Failed to read {VERCEL_CONFIG_PATH}: {e}")
        return None

    @classmethod
    async def check_token_status(cls) -> Dict[str, Any]:
        """
        Validates the Vercel Personal Access Token by calling https://api.vercel.com/v2/user.
        Reports whether the token is valid/active, or if it has expired (401/403).
        """
        token = cls.get_token()
        if not token:
            return {
                "status": "missing",
                "valid": False,
                "message": f"No Vercel Personal Access Token configured in {VERCEL_CONFIG_PATH} or VERCEL_TOKEN env.",
                "config_path": VERCEL_CONFIG_PATH
            }

        def _probe():
            import urllib.request
            import urllib.error
            url = "https://api.vercel.com/v2/user"
            req = urllib.request.Request(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "User-Agent": "Ghost-Agent/1.0"
                }
            )
            try:
                with urllib.request.urlopen(req, timeout=8) as resp:
                    data = json.loads(resp.read().decode())
                    user_info = data.get("user", {})
                    return {
                        "status": "valid",
                        "valid": True,
                        "username": user_info.get("username"),
                        "email": user_info.get("email"),
                        "name": user_info.get("name"),
                        "message": f"Vercel token is active and valid for user '{user_info.get('username', 'operator')} ({user_info.get('email', '')})'."
                    }
            except urllib.error.HTTPError as e:
                if e.code in [401, 403]:
                    return {
                        "status": "expired",
                        "valid": False,
                        "code": e.code,
                        "message": f"Vercel Personal Access Token has EXPIRED or is unauthorized (HTTP {e.code}). 30-day token renewal required at https://vercel.com/account/tokens."
                    }
                return {
                    "status": "error",
                    "valid": False,
                    "code": e.code,
                    "message": f"Vercel API error HTTP {e.code}: {e.reason}"
                }
            except Exception as e:
                return {
                    "status": "error",
                    "valid": False,
                    "message": f"Network or connection error probing Vercel: {e}"
                }

        return await asyncio.to_thread(_probe)

    @classmethod
    def save_token(cls, token: str) -> None:
        """Save Vercel Personal Access Token to configs/vercel.json."""
        os.makedirs(CONFIGS_DIR, exist_ok=True)
        cfg = {}
        if os.path.exists(VERCEL_CONFIG_PATH):
            try:
                with open(VERCEL_CONFIG_PATH, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception:
                pass
        cfg["vercel_token"] = token.strip()
        with open(VERCEL_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)

    @classmethod
    async def deploy_demo(cls, slug: str, project_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Deploys a demo directory (`demos/<slug>`) to Vercel production using bunx vercel.
        Uses neutral project naming ({slug}-concept) without internal branding.
        Requires Tactical Authorization before cloud dispatch.
        """
        demo_dir = os.path.join(DEMOS_DIR, slug)
        if not os.path.exists(demo_dir):
            return {"status": "error", "message": f"Demo directory not found at '{demo_dir}'"}

        token = cls.get_token()
        if not token:
            return {
                "status": "config_required",
                "message": (
                    f"Vercel deployment token missing. "
                    f"To enable one-click cloud deployment, generate a Personal Access Token at https://vercel.com/account/tokens "
                    f"and store it in '{VERCEL_CONFIG_PATH}' as {{\"vercel_token\": \"<TOKEN>\"}} or set VERCEL_TOKEN env."
                ),
                "config_path": VERCEL_CONFIG_PATH,
                "url": None
            }

        # Neutral project name: never leak 'ghost' into client-facing URLs
        p_name = project_name or f"{slug}-concept"

        # Require tactical authorization before dispatching public deployment
        allowed = await request_tactical_authorization(
            f"Deploy live public demo '{slug}' to Vercel production (Project: {p_name})"
        )
        if not allowed:
            return {"status": "denied", "message": "Vercel deployment aborted per operator orders.", "url": None}

        # Resolve bunx binary dynamically
        bunx_bin = shutil.which("bunx")
        if not bunx_bin:
            home_bunx = os.path.expanduser("~/.bun/bin/bunx")
            bunx_bin = home_bunx if os.path.exists(home_bunx) else "bunx"

        cmd = f'cd "{demo_dir}" && {bunx_bin} --yes vercel deploy --prod --yes --token "{token}" --name "{p_name}"'
        logger.info(f"Executing Vercel deployment for '{slug}' (Project: {p_name})...")

        res = await ShellExecutor.run(cmd, timeout=90.0)
        if res.get("success"):
            stdout = res.get("stdout", "")
            urls = [line.strip() for line in stdout.splitlines() if line.strip().startswith("https://")]
            deploy_url = urls[-1] if urls else stdout.strip()

            try:
                from ghost.crm.db import CRMDatabase
                crm = CRMDatabase.get_instance()
                crm.log_interaction(
                    client_slug=slug,
                    channel="vercel",
                    direction="outbound",
                    content=f"Deployed live demo to Vercel: {deploy_url}"
                )
            except Exception:
                pass

            return {
                "status": "success",
                "slug": slug,
                "project_name": p_name,
                "url": deploy_url,
                "output": stdout[:300]
            }
        else:
            return {
                "status": "error",
                "message": f"Vercel deployment failed: {res.get('stderr') or res.get('stdout')}",
                "raw": res,
                "url": None
            }
