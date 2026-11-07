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
import time
from typing import Dict, Any, Optional
from ghost.mcp.bridge import ShellExecutor, request_tactical_authorization

logger = logging.getLogger("ghost.workers.deploy")

CONFIGS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../configs"))
VERCEL_CONFIG_PATH = os.path.join(CONFIGS_DIR, "vercel.json")
DEFAULT_DEMOS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../demos"))
DEMOS_DIR = os.environ.get("GHOST_DEMOS_DIR") or DEFAULT_DEMOS_DIR


def _demos_dir() -> str:
    """Re-resolved per call (env redirects / test isolation)."""
    return os.environ.get("GHOST_DEMOS_DIR") or DEFAULT_DEMOS_DIR


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
    def _qa_hash(cls, demo_dir: str) -> str:
        """Stable hash of the QA evidence a deploy is riding on — one deploy
        per evidence; new screenshots mean a new hash and a new deploy."""
        import hashlib
        qa_path = os.path.join(demo_dir, "reports", "qa.json")
        try:
            with open(qa_path, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()
        except OSError:
            return "no-qa"

    @classmethod
    def _snapshot_gate(cls, slug: str) -> Optional[Dict[str, Any]]:
        """T2 precondition: never touch a live site without a fresh rollback
        point. Missing or stale (>24h) snapshot -> refuse with the remedy."""
        from ghost.core.audit import audit
        snap, latest_error = None, "none"
        try:
            from ghost.workers.snapshots import SnapshotStore
            snap = SnapshotStore().latest(slug)
        except Exception as e:
            latest_error = str(e)
        if not snap:
            audit("deploy.snapshot_required", subject=slug,
                  reason="no_snapshot", detail=latest_error[:200])
            return {
                "status": "snapshot_required",
                "message": (f"Deploy of '{slug}' refused: no snapshot exists. "
                            "Snapshot the demo dir first (ghost.workers.snapshots."
                            "SnapshotManager.snapshot) — deploys require a rollback point."),
                "url": None,
            }
        age_s = time.time() - float(snap.get("created_at", 0))
        if age_s > 24 * 3600:
            audit("deploy.snapshot_required", subject=slug,
                  reason="stale_snapshot", age_h=round(age_s / 3600, 1))
            return {
                "status": "snapshot_required",
                "message": (f"Deploy of '{slug}' refused: newest snapshot is "
                            f"{age_s / 3600:.1f}h old (>24h). Take a fresh snapshot."),
                "url": None,
            }
        return None

    @staticmethod
    def _qa_gate(slug: str, demo_dir: str, allow_unverified: bool = False) -> Optional[Dict[str, Any]]:
        """Returns None if QA evidence clears the build for deploy, else a
        refusal dict. Evidence contract: passed=true AND screenshots_captured>0
        AND every cited screenshot file exists on disk."""
        qa_path = os.path.join(demo_dir, "reports", "qa.json")
        try:
            from ghost.core.audit import audit
        except Exception:
            audit = None

        def refuse(reason: str) -> Dict[str, Any]:
            if audit:
                audit("deploy.blocked_by_qa", subject=slug, reason=reason)
            return {
                "status": "blocked_by_qa",
                "message": (f"Deploy refused for '{slug}': {reason}. Run ghost.workers.three_qa "
                            "first; deploys require passed visual QA with screenshots."),
                "url": None,
            }

        if not os.path.exists(qa_path):
            if allow_unverified:
                if audit:
                    audit("deploy.unverified_override", subject=slug, reason="no_qa_report")
                return None
            return refuse("no QA report found")

        try:
            with open(qa_path, "r", encoding="utf-8") as f:
                qa = json.load(f)
        except Exception as e:
            return refuse(f"QA report unreadable ({e})")

        if not qa.get("passed"):
            return refuse(f"QA passed=false — failures: {qa.get('failures')}")

        # Evidence freshness (the Grasshopper lesson): a QA report OLDER than
        # the page it certifies is stale evidence — it describes a build that
        # no longer exists. Hand-edited demos cannot produce fresh machine QA,
        # so this gate is what makes the state machine the only deploy route.
        index_path = os.path.join(demo_dir, "index.html")
        if os.path.exists(index_path):
            qa_age = os.path.getmtime(qa_path)
            page_age = os.path.getmtime(index_path)
            if qa_age < page_age:
                return refuse(
                    f"stale QA evidence — reports/qa.json ({qa_age:.0f}) predates "
                    f"index.html ({page_age:.0f}). The page was modified after its "
                    "last QA pass. Rebuild via the demo state machine "
                    "(build_luxury_demo) to produce fresh machine QA.")

        captured = int(qa.get("screenshots_captured", 0) or 0)
        if captured <= 0:
            return refuse("QA passed but captured zero screenshots — evidence contract violated")

        # Every cited screenshot must exist. A pass citing missing files is a fake pass.
        shots = (qa.get("human_review") or {}).get("screenshots", [])
        missing = [s for s in shots if not os.path.exists(os.path.join(demo_dir, s))]
        if missing:
            return refuse(f"QA cites {len(missing)} screenshot(s) missing on disk")

        if allow_unverified:
            if audit:
                audit("deploy.unverified_override", subject=slug, reason="operator_override_passed_qa_exists")
        elif audit:
            audit("deploy.qa_gate_cleared", subject=slug, screenshots=captured)
        return None

    @classmethod
    async def deploy_demo(cls, slug: str, project_name: Optional[str] = None,
                          allow_unverified: bool = False,
                          grant_token: Optional[str] = None) -> Dict[str, Any]:
        """
        Deploys a demo directory (`demos/<slug>`) to Vercel production using bunx vercel.
        Uses neutral project naming ({slug}-concept) without internal branding.
        Requires (in order): halt clear -> a valid single-use DEPLOY GRANT ->
        a FRESH SNAPSHOT of the demo dir -> passed screenshot QA -> Tactical
        Authorization. allow_unverified=True is the operator's explicit QA
        override — it is logged either way; it does NOT bypass grant/snapshot.
        """
        demo_dir = os.path.join(_demos_dir(), slug)
        if not os.path.exists(demo_dir):
            return {"status": "error", "message": f"Demo directory not found at '{demo_dir}'"}

        # ---- Halt gate (P0): the throat-cut handle freezes deploys too. ------
        from ghost.core.halt import assert_not_halted
        try:
            assert_not_halted(f"deploy_demo:{slug}")
        except Exception as e:
            return {"status": "halted", "message": str(e), "url": None}

        # ---- Grant gate (T2): a deploy is an effect — no grant, no deploy. ---
        from ghost.core import grants
        bounds = grants.verify(grant_token or "", capability="deploy", subject=slug)
        if bounds is None:
            from ghost.core.audit import audit
            audit("deploy.grant_required", subject=slug,
                  reason="no_valid_deploy_grant")
            return {
                "status": "grant_required",
                "message": (f"Deploy of '{slug}' refused: no valid deploy grant. "
                            "Request one via "
                            "ghost.autonomy.outbound.issue_effect_grant('deploy', slug) "
                            "— the control plane (not the LLM) authorizes effects."),
                "url": None,
            }

        # ---- Snapshot precondition (T2): touch nothing without a rollback. ---
        snapshot_block = cls._snapshot_gate(slug)
        if snapshot_block:
            return snapshot_block

        # ---- QA gate (P2, Roadmap §2 Stage 2): deploys are EVIDENCE-gated. ---
        # No passed QA report with captured screenshots -> refuse. Deploying an
        # unverified build is exactly how "something feels off" reached clients.
        qa_block = cls._qa_gate(slug, demo_dir, allow_unverified=allow_unverified)
        if qa_block:
            return qa_block

        # ---- Outbox (T1c protocol): record BEFORE dispatching. One deploy per
        # QA evidence — a retry with unchanged evidence hits the 'sent' row.
        from ghost.core.audit import audit
        qa_hash = cls._qa_hash(demo_dir)
        ob_key = f"deploy:{slug}:{qa_hash[:12]}"
        from ghost.store import outbox as _outbox
        if _outbox.state_of(ob_key) in ("sent", "verified"):
            audit("deploy.duplicate_refused", subject=slug, outbox_key=ob_key)
            return {"status": "already_deployed",
                    "message": f"Deploy for '{slug}' already recorded for this QA evidence.",
                    "url": None}
        _outbox.record("deploy", ob_key, {"slug": slug, "qa_hash": qa_hash[:12]})

        token = cls.get_token()
        if not token:
            preview_file = f"file://{os.path.abspath(demo_dir)}/index.html"
            return {
                "status": "config_required",
                "message": (
                    f"Vercel deployment token missing. "
                    f"To enable one-click cloud deployment, generate a Personal Access Token at https://vercel.com/account/tokens "
                    f"and store it in '{VERCEL_CONFIG_PATH}' as {{\"vercel_token\": \"<TOKEN>\"}} or set VERCEL_TOKEN env."
                ),
                "config_path": VERCEL_CONFIG_PATH,
                "url": None,
                "internal_preview_path": preview_file
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

        # T3: LAST halt re-check — immediately before dispatch, not at entry.
        # The grant is consumed; this check is what stops the effect itself.
        from ghost.core.halt import is_halted
        if is_halted():
            _outbox.transition(ob_key, "failed")
            audit("deploy.halt_pre_dispatch", subject=slug, outbox_key=ob_key)
            return {"status": "halted",
                    "message": f"Deploy of '{slug}' stopped: halt tripped between "
                               "authorization and dispatch.",
                    "url": None}

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

            _outbox.transition(ob_key, "sent")
            return {
                "status": "success",
                "slug": slug,
                "project_name": p_name,
                "url": deploy_url,
                "output": stdout[:300]
            }
        else:
            _outbox.transition(ob_key, "failed")
            return {
                "status": "error",
                "message": f"Vercel deployment failed: {res.get('stderr') or res.get('stdout')}",
                "raw": res,
                "url": None
            }
