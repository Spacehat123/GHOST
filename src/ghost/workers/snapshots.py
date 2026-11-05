"""Snapshot Discipline — backups that have PROVEN they restore (Roadmap §2 Stage 7).

A snapshot without a passed restore-drill is a hope, not a backup. Rules:
- every snapshot records sha256 per file (integrity is verified, not assumed)
- restore verifies every hash during copy; a mismatch aborts the restore
- a snapshot is only marked `drill_verified_at` after a drill restored it into
  a sandbox and re-verified every hash
- `drill_due` enforces the monthly drill (§2 Stage 7: monthly random restore
  test into a sandbox — evidence, again)

No touch of a live client site is permitted without a snapshot (site_monitor
enforces this before any auto-fix).
"""
import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
from typing import Any, Dict, List, Optional

from ghost.core.audit import audit

logger = logging.getLogger("ghost.snapshots")

REPO_DIR = os.path.abspath(os.environ.get("GHOST_PROJECT_ROOT") or os.path.join(os.path.dirname(__file__), "../../.."))
SNAPSHOTS_DIR = os.environ.get("GHOST_SNAPSHOTS_DIR") or os.path.join(REPO_DIR, "data", "snapshots")
DRILL_INTERVAL_S = 30 * 86400  # monthly


def _snapshots_dir() -> str:
    """Re-resolved per call (test isolation / env redirects)."""
    return os.environ.get("GHOST_SNAPSHOTS_DIR") or os.path.join(REPO_DIR, "data", "snapshots")


def _hash_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class SnapshotStore:
    def __init__(self, base_dir: Optional[str] = None):
        self.base_dir = base_dir or _snapshots_dir()
        os.makedirs(self.base_dir, exist_ok=True)

    def _slug_dir(self, slug: str) -> str:
        return os.path.join(self.base_dir, slug)

    # ------------------------------------------------------------------ create

    def snapshot(self, slug: str, source_dir: str, label: str = "scheduled") -> Dict[str, Any]:
        """Snapshot all files under source_dir. Returns evidence with hashes."""
        if not os.path.isdir(source_dir):
            raise FileNotFoundError(f"nothing to snapshot at {source_dir}")
        ts = time.strftime("%Y%m%d-%H%M%S")
        snap_id = f"{ts}-{label}"
        dest = os.path.join(self._slug_dir(slug), snap_id)
        os.makedirs(dest, exist_ok=True)
        manifest = {"slug": slug, "snapshot_id": snap_id, "created_at": time.time(),
                    "label": label, "files": {}}
        for root, _dirs, files in os.walk(source_dir):
            for name in files:
                src_path = os.path.join(root, name)
                rel = os.path.relpath(src_path, source_dir)
                dst_path = os.path.join(dest, rel)
                os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                shutil.copy2(src_path, dst_path)
                manifest["files"][rel] = {"sha256": _hash_file(dst_path),
                                          "size": os.path.getsize(dst_path)}
        with open(os.path.join(dest, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
        audit("snapshot.created", subject=slug, snapshot_id=snap_id,
              files=len(manifest["files"]), label=label)
        return manifest

    # ------------------------------------------------------------------ query

    def latest(self, slug: str) -> Optional[Dict[str, Any]]:
        snaps = self.list_snapshots(slug)
        return snaps[-1] if snaps else None

    def list_snapshots(self, slug: str) -> List[Dict[str, Any]]:
        d = self._slug_dir(slug)
        if not os.path.isdir(d):
            return []
        out = []
        for snap_id in sorted(os.listdir(d)):
            mpath = os.path.join(d, snap_id, "manifest.json")
            if os.path.exists(mpath):
                try:
                    with open(mpath, "r", encoding="utf-8") as f:
                        out.append(json.load(f))
                except Exception:
                    continue
        return out

    # ---------------------------------------------------------------- restore

    def restore(self, slug: str, snapshot_id: str, dest_dir: str) -> Dict[str, Any]:
        """Restore a snapshot into dest_dir, verifying every hash en route.
        Any mismatch aborts BEFORE writing anything to dest_dir."""
        snap_dir = os.path.join(self._slug_dir(slug), snapshot_id)
        mpath = os.path.join(snap_dir, "manifest.json")
        if not os.path.exists(mpath):
            raise FileNotFoundError(f"snapshot {snapshot_id} not found for {slug}")
        with open(mpath, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        # verify sources first — never half-restore
        bad = []
        for rel, meta in manifest["files"].items():
            src_path = os.path.join(snap_dir, rel)
            if not os.path.exists(src_path) or _hash_file(src_path) != meta["sha256"]:
                bad.append(rel)
        if bad:
            audit("snapshot.restore_aborted", subject=slug, snapshot_id=snapshot_id,
                  corrupt_files=bad[:10])
            raise RuntimeError(f"snapshot integrity failed for {len(bad)} file(s): {bad[:5]}")

        os.makedirs(dest_dir, exist_ok=True)
        restored = 0
        for rel, _meta in manifest["files"].items():
            dst_path = os.path.join(dest_dir, rel)
            os.makedirs(os.path.dirname(dst_path) or dest_dir, exist_ok=True)
            shutil.copy2(os.path.join(snap_dir, rel), dst_path)
            if _hash_file(dst_path) != manifest["files"][rel]["sha256"]:
                raise RuntimeError(f"restore verification failed for {rel}")
            restored += 1
        audit("snapshot.restored", subject=slug, snapshot_id=snapshot_id,
              files=restored, dest=dest_dir)
        return {"restored_files": restored, "dest": dest_dir,
                "snapshot_id": snapshot_id, "verified": True}

    # ------------------------------------------------------------------ drill

    def restore_drill(self, slug: str, snapshot_id: Optional[str] = None) -> Dict[str, Any]:
        """Prove the backup works: restore into a throwaway sandbox, verify
        hashes, mark the snapshot drill_verified_at. This is the evidence that
        turns 'we have backups' into 'we can recover'."""
        snap = snapshot_id or (self.latest(slug) or {}).get("snapshot_id")
        if not snap:
            audit("snapshot.drill_failed", subject=slug, reason="no snapshot exists")
            return {"ok": False, "reason": "no snapshot exists"}
        with tempfile.TemporaryDirectory(prefix="ghost-drill-") as sandbox:
            try:
                result = self.restore(slug, snap, sandbox)
            except Exception as e:
                audit("snapshot.drill_failed", subject=slug, snapshot_id=snap,
                      error=str(e)[:300])
                return {"ok": False, "snapshot_id": snap, "error": str(e)[:300]}
        mpath = os.path.join(self._slug_dir(slug), snap, "manifest.json")
        with open(mpath, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        manifest["drill_verified_at"] = time.time()
        with open(mpath, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
        audit("snapshot.drill_passed", subject=slug, snapshot_id=snap,
              files=result["restored_files"])
        return {"ok": True, "snapshot_id": snap, **result}

    def drill_due(self, slug: str) -> bool:
        """True if the newest drill-verified snapshot is older than a month, or
        no snapshot has ever been drilled."""
        for m in reversed(self.list_snapshots(slug)):
            verified_at = m.get("drill_verified_at")
            if verified_at and time.time() - verified_at < DRILL_INTERVAL_S:
                return False
        return True
