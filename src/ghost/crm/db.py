"""SQLite CRM Engine & Bi-Directional Obsidian Vault Sync for Ghost.
Single source of truth for hospitality & boutique web agency client tracking.
Guarantees anti-hallucination: all pricing, deals, and channel preferences are strictly grounded.
"""
import os
import re
import json
import uuid
import sqlite3
import time
import logging
from contextlib import contextmanager
from typing import Dict, Any, List, Optional, Tuple, Union

logger = logging.getLogger("ghost.crm")

REPO_DIR = os.path.abspath(os.environ.get("GHOST_PROJECT_ROOT") or os.path.join(os.path.dirname(__file__), "../../.."))
CRM_DB_PATH = os.environ.get("GHOST_CRM_DB_PATH") or os.path.join(REPO_DIR, "data", "crm.db")
OBSIDIAN_CLIENTS_DIR = os.environ.get("GHOST_OBSIDIAN_DIR") or os.path.expanduser("~/Obsidian Vault/Ghost/Clients")

LEAD_STATUSES = {
    "harvested", "qualified", "rejected", "building", "needs_confirmation",
    "built", "deployed", "drafts_ready", "approved", "sent", "replied",
    "agreed", "contract_drafted", "contract_sent", "deposit_received",
    "won", "lost", "suppressed"
}


class CRMDatabase:
    _instance: Optional["CRMDatabase"] = None

    def __init__(self, db_path: str = CRM_DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        os.makedirs(OBSIDIAN_CLIENTS_DIR, exist_ok=True)
        self._init_db()

    @classmethod
    def get_instance(cls) -> "CRMDatabase":
        # Re-resolve the path from env on every call (audit/secrets pattern) —
        # a static import-time bind leaked suite tests into the LIVE crm.db
        # (T10 preflight caught 32 orphaned test drafts). An EXTERNALLY
        # injected instance (tests patch _instance directly) is respected as-is.
        inst = cls._instance
        if inst is not None and not getattr(inst, "_ghost_managed", False):
            return inst
        db_path = os.environ.get("GHOST_CRM_DB_PATH") or CRM_DB_PATH
        if inst is None or inst.db_path != db_path:
            cls._instance = CRMDatabase(db_path)
            cls._instance._ghost_managed = True
        return cls._instance

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self):
        """Create tables and indices if they do not exist."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS clients (
                    slug TEXT PRIMARY KEY,
                    business_name TEXT NOT NULL,
                    contact_name TEXT,
                    email TEXT,
                    phone TEXT,
                    preferred_channel TEXT DEFAULT 'email',
                    status TEXT DEFAULT 'unscouted',
                    deal_value REAL DEFAULT 0.0,
                    notes TEXT,
                    created_at REAL,
                    updated_at REAL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS interactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_slug TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp REAL,
                    FOREIGN KEY (client_slug) REFERENCES clients(slug) ON DELETE CASCADE
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS campaigns (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    city TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    harvest_per_batch INTEGER DEFAULT 100,
                    keep_fraction REAL DEFAULT 0.33
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS batches (
                    id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    harvested_count INTEGER DEFAULT 0,
                    qualified_count INTEGER DEFAULT 0,
                    built_count INTEGER DEFAULT 0,
                    created_at REAL,
                    UNIQUE(campaign_id, category),
                    FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS leads (
                    id TEXT PRIMARY KEY,
                    batch_id TEXT,
                    slug TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    address TEXT,
                    phone TEXT,
                    website TEXT,
                    rating REAL,
                    review_count INTEGER,
                    maps_cid TEXT,
                    google_category TEXT,
                    domain TEXT,
                    entity_type TEXT DEFAULT 'unknown',
                    score_revenue REAL DEFAULT 0.0,
                    score_fit REAL DEFAULT 0.0,
                    score_friction REAL DEFAULT 0.0,
                    score_total REAL DEFAULT 0.0,
                    gaps_json TEXT,
                    status TEXT DEFAULT 'harvested',
                    needs_confirmation INTEGER DEFAULT 0,
                    created_at REAL NOT NULL,
                    demo_url TEXT,
                    FOREIGN KEY (batch_id) REFERENCES batches(id) ON DELETE SET NULL
                )
            """)
            try:
                cur.execute("ALTER TABLE leads ADD COLUMN demo_url TEXT")
            except sqlite3.OperationalError:
                pass
            cur.execute("""
                CREATE TABLE IF NOT EXISTS suppression (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    value_normalised TEXT UNIQUE NOT NULL,
                    reason TEXT NOT NULL,
                    added_at REAL NOT NULL,
                    source TEXT DEFAULT 'manual'
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS drafts (
                    id TEXT PRIMARY KEY,
                    lead_id TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    subject TEXT,
                    body TEXT NOT NULL,
                    link TEXT,
                    status TEXT NOT NULL DEFAULT 'draft',
                    created_at REAL NOT NULL,
                    approved_at REAL,
                    sent_at REAL,
                    FOREIGN KEY (lead_id) REFERENCES leads(id) ON DELETE CASCADE
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    lead_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    version INTEGER DEFAULT 1,
                    path TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft',
                    created_at REAL NOT NULL,
                    FOREIGN KEY (lead_id) REFERENCES leads(id) ON DELETE CASCADE
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    lead_id TEXT,
                    type TEXT NOT NULL,
                    payload_json TEXT,
                    at REAL NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS processed_events (
                    event_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    processed_at REAL NOT NULL
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_leads_batch ON leads(batch_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_leads_slug ON leads(slug)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_suppression_lookup ON suppression(kind, value_normalised)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_events_lead ON events(lead_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(type)")
            conn.commit()

    # =========================================================================
    # Campaigns & Batches
    # =========================================================================

    def create_campaign(
        self,
        name: str,
        city: str,
        harvest_per_batch: int = 100,
        keep_fraction: float = 0.33,
        campaign_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a new campaign."""
        cid = campaign_id or f"camp_{uuid.uuid4().hex[:10]}"
        now = time.time()
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO campaigns (id, name, city, created_at, harvest_per_batch, keep_fraction)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (cid, name.strip(), city.strip(), now, harvest_per_batch, keep_fraction))
            conn.commit()
        return self.get_campaign(cid)

    def get_campaign(self, campaign_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve campaign by ID with its batches."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id.strip(),))
            row = cur.fetchone()
            if not row:
                return None
            data = dict(row)
            cur.execute("SELECT * FROM batches WHERE campaign_id = ? ORDER BY created_at ASC, id ASC", (campaign_id.strip(),))
            data["batches"] = [dict(b) for b in cur.fetchall()]
            return data

    def list_campaigns(self) -> List[Dict[str, Any]]:
        """List all campaigns ordered by creation date."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM campaigns ORDER BY created_at DESC")
            campaigns = [dict(r) for r in cur.fetchall()]
            for c in campaigns:
                cur.execute("SELECT * FROM batches WHERE campaign_id = ? ORDER BY created_at ASC, id ASC", (c["id"],))
                c["batches"] = [dict(b) for b in cur.fetchall()]
            return campaigns

    def create_batch(
        self,
        campaign_id: str,
        category: str,
        batch_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a new batch under a campaign."""
        now = time.time()
        bid = batch_id or f"batch_{int(now*1000)}_{uuid.uuid4().hex[:6]}"
        clean_cat = category.strip()
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO batches (id, campaign_id, category, status, harvested_count, qualified_count, built_count, created_at)
                VALUES (?, ?, ?, 'pending', 0, 0, 0, ?)
            """, (bid, campaign_id.strip(), clean_cat, now))
            conn.commit()
        return self.get_batch(bid)

    def get_batch(self, batch_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve batch by ID."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM batches WHERE id = ?", (batch_id.strip(),))
            row = cur.fetchone()
            return dict(row) if row else None

    def get_batches_for_campaign(self, campaign_id: str) -> List[Dict[str, Any]]:
        """Get all batches for a given campaign."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM batches WHERE campaign_id = ? ORDER BY created_at ASC, id ASC", (campaign_id.strip(),))
            return [dict(r) for r in cur.fetchall()]

    def update_batch(
        self,
        batch_id: str,
        status: Optional[str] = None,
        harvested_count: Optional[int] = None,
        qualified_count: Optional[int] = None,
        built_count: Optional[int] = None
    ) -> bool:
        """Update batch status and counters."""
        updates = []
        params = []
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if harvested_count is not None:
            updates.append("harvested_count = ?")
            params.append(harvested_count)
        if qualified_count is not None:
            updates.append("qualified_count = ?")
            params.append(qualified_count)
        if built_count is not None:
            updates.append("built_count = ?")
            params.append(built_count)

        if not updates:
            return False

        params.append(batch_id.strip())
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(f"UPDATE batches SET {', '.join(updates)} WHERE id = ?", params)
            conn.commit()
            return cur.rowcount > 0

    # =========================================================================
    # Leads & Clients
    # =========================================================================

    def add_or_update_lead(self, lead_data: Dict[str, Any]) -> Dict[str, Any]:
        """Insert or update a lead record in the leads table."""
        slug = lead_data["slug"].strip().lower()
        lid = lead_data.get("id") or f"lead_{uuid.uuid4().hex[:12]}"
        now = time.time()
        created_at = lead_data.get("created_at") or now

        # Package any extra metadata into gaps_json if provided
        gaps_json = lead_data.get("gaps_json")
        extra = {}
        for k in ("audit_data", "instagram", "facebook", "has_social", "email", "legal_name", "deal_value", "contact_name", "deal_overrides"):
            if lead_data.get(k) is not None:
                extra[k] = lead_data[k]
        if extra:
            if gaps_json:
                try:
                    existing_dict = json.loads(gaps_json)
                    if isinstance(existing_dict, dict):
                        existing_dict.update(extra)
                        gaps_json = json.dumps(existing_dict)
                except Exception:
                    pass
            else:
                gaps_json = json.dumps(extra)

        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, gaps_json FROM leads WHERE slug = ?", (slug,))
            existing = cur.fetchone()

            if existing:
                if existing["gaps_json"]:
                    try:
                        base_dict = json.loads(existing["gaps_json"])
                        if isinstance(base_dict, dict):
                            base_dict.update(extra)
                            gaps_json = json.dumps(base_dict)
                    except Exception:
                        pass
                cur.execute("""
                    UPDATE leads SET
                        batch_id = COALESCE(?, batch_id),
                        name = COALESCE(?, name),
                        address = COALESCE(?, address),
                        phone = COALESCE(?, phone),
                        website = COALESCE(?, website),
                        rating = COALESCE(?, rating),
                        review_count = COALESCE(?, review_count),
                        maps_cid = COALESCE(?, maps_cid),
                        google_category = COALESCE(?, google_category),
                        domain = COALESCE(?, domain),
                        entity_type = COALESCE(?, entity_type),
                        score_revenue = COALESCE(?, score_revenue),
                        score_fit = COALESCE(?, score_fit),
                        score_friction = COALESCE(?, score_friction),
                        score_total = COALESCE(?, score_total),
                        gaps_json = COALESCE(?, gaps_json),
                        status = COALESCE(?, status),
                        needs_confirmation = COALESCE(?, needs_confirmation),
                        demo_url = COALESCE(?, demo_url)
                    WHERE slug = ?
                """, (
                    lead_data.get("batch_id"),
                    lead_data.get("name"),
                    lead_data.get("address"),
                    lead_data.get("phone"),
                    lead_data.get("website"),
                    lead_data.get("rating"),
                    lead_data.get("review_count"),
                    lead_data.get("maps_cid"),
                    lead_data.get("google_category"),
                    lead_data.get("domain"),
                    lead_data.get("entity_type", "unknown"),
                    lead_data.get("score_revenue", 0.0),
                    lead_data.get("score_fit", 0.0),
                    lead_data.get("score_friction", 0.0),
                    lead_data.get("score_total", 0.0),
                    gaps_json,
                    lead_data.get("status", "harvested"),
                    1 if lead_data.get("needs_confirmation") else 0,
                    lead_data.get("demo_url"),
                    slug
                ))
            else:
                cur.execute("""
                    INSERT INTO leads (
                        id, batch_id, slug, name, address, phone, website,
                        rating, review_count, maps_cid, google_category, domain,
                        entity_type, score_revenue, score_fit, score_friction,
                        score_total, gaps_json, status, needs_confirmation, created_at, demo_url
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    lid,
                    lead_data.get("batch_id"),
                    slug,
                    lead_data.get("name"),
                    lead_data.get("address"),
                    lead_data.get("phone"),
                    lead_data.get("website"),
                    lead_data.get("rating"),
                    lead_data.get("review_count"),
                    lead_data.get("maps_cid"),
                    lead_data.get("google_category"),
                    lead_data.get("domain"),
                    lead_data.get("entity_type", "unknown"),
                    lead_data.get("score_revenue", 0.0),
                    lead_data.get("score_fit", 0.0),
                    lead_data.get("score_friction", 0.0),
                    lead_data.get("score_total", 0.0),
                    gaps_json,
                    lead_data.get("status", "harvested"),
                    1 if lead_data.get("needs_confirmation") else 0,
                    created_at,
                    lead_data.get("demo_url")
                ))
            conn.commit()

        # Keep legacy clients table synchronized
        self.add_or_update_client(
            slug=slug,
            business_name=lead_data.get("name") or slug,
            phone=lead_data.get("phone"),
            email=lead_data.get("email"),
            status=lead_data.get("status", "harvested")
        )

        return self.get_lead(slug)

    def get_lead(self, slug_or_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve lead by slug or ID."""
        clean_key = slug_or_id.strip()
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM leads WHERE slug = ? OR id = ?", (clean_key.lower(), clean_key))
            row = cur.fetchone()
            if not row:
                return None
            data = dict(row)
            data["needs_confirmation"] = bool(data.get("needs_confirmation"))
            if data.get("gaps_json"):
                try:
                    extra = json.loads(data["gaps_json"])
                    if isinstance(extra, dict):
                        for k in ("audit_data", "instagram", "facebook", "has_social", "email", "legal_name", "deal_value", "contact_name", "deal_overrides"):
                            if k in extra and k not in data:
                                data[k] = extra[k]
                except Exception:
                    pass
            return data

    def get_leads_by_batch(self, batch_id: str, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieve all leads for a batch, optionally filtered by status."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            if status:
                cur.execute(
                    "SELECT * FROM leads WHERE batch_id = ? AND status = ? ORDER BY score_total DESC, created_at ASC",
                    (batch_id.strip(), status.strip().lower())
                )
            else:
                cur.execute(
                    "SELECT * FROM leads WHERE batch_id = ? ORDER BY score_total DESC, created_at ASC",
                    (batch_id.strip(),)
                )
            rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                r["needs_confirmation"] = bool(r.get("needs_confirmation"))
                if r.get("gaps_json"):
                    try:
                        extra = json.loads(r["gaps_json"])
                        if isinstance(extra, dict):
                            for k in ("audit_data", "instagram", "facebook", "has_social", "email", "legal_name", "deal_value", "contact_name", "deal_overrides"):
                                if k in extra and k not in r:
                                    r[k] = extra[k]
                    except Exception:
                        pass
            return rows

    def get_all_leads(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieve all leads, optionally filtered by status."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            if status:
                cur.execute("SELECT * FROM leads WHERE status = ? ORDER BY score_total DESC, created_at ASC", (status.strip().lower(),))
            else:
                cur.execute("SELECT * FROM leads ORDER BY score_total DESC, created_at ASC")
            rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                r["needs_confirmation"] = bool(r.get("needs_confirmation"))
                if r.get("gaps_json"):
                    try:
                        extra = json.loads(r["gaps_json"])
                        if isinstance(extra, dict):
                            for k in ("audit_data", "instagram", "facebook", "has_social", "email", "legal_name", "deal_value", "contact_name", "deal_overrides"):
                                if k in extra and k not in r:
                                    r[k] = extra[k]
                    except Exception:
                        pass
            return rows

    def update_lead(self, slug_or_id: str, **kwargs) -> bool:
        """Update arbitrary columns of a lead."""
        if not kwargs:
            return False
        clean_key = slug_or_id.strip()
        updates = []
        params = []
        for k, v in kwargs.items():
            if k == "needs_confirmation":
                updates.append("needs_confirmation = ?")
                params.append(1 if v else 0)
            else:
                updates.append(f"{k} = ?")
                params.append(v)

        params.extend([clean_key.lower(), clean_key])
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(f"UPDATE leads SET {', '.join(updates)} WHERE slug = ? OR id = ?", params)
            conn.commit()
            updated = cur.rowcount > 0

        if updated and "status" in kwargs:
            self.update_lead_status(clean_key, kwargs["status"])

        return updated

    def add_or_update_client(
        self,
        slug: str,
        business_name: str,
        contact_name: Optional[str] = None,
        email: Optional[str] = None,
        phone: Optional[str] = None,
        preferred_channel: str = "email",
        status: str = "unscouted",
        deal_value: float = 0.0,
        notes: Optional[str] = None
    ) -> Dict[str, Any]:
        """Insert or update a client record and sync to Obsidian."""
        clean_slug = slug.strip().lower()
        now = time.time()

        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM clients WHERE slug = ?", (clean_slug,))
            existing = cur.fetchone()

            if existing:
                cur.execute("""
                    UPDATE clients SET
                        business_name = COALESCE(?, business_name),
                        contact_name = COALESCE(?, contact_name),
                        email = COALESCE(?, email),
                        phone = COALESCE(?, phone),
                        preferred_channel = COALESCE(?, preferred_channel),
                        status = COALESCE(?, status),
                        deal_value = COALESCE(?, deal_value),
                        notes = COALESCE(?, notes),
                        updated_at = ?
                    WHERE slug = ?
                """, (
                    business_name, contact_name, email, phone,
                    preferred_channel, status, deal_value, notes,
                    now, clean_slug
                ))
            else:
                cur.execute("""
                    INSERT INTO clients (
                        slug, business_name, contact_name, email, phone,
                        preferred_channel, status, deal_value, notes,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    clean_slug, business_name, contact_name, email, phone,
                    preferred_channel, status, deal_value, notes or "",
                    now, now
                ))
            conn.commit()

        client_data = self.get_client(clean_slug)
        if client_data:
            self._sync_to_obsidian(client_data)

        return client_data

    def update_lead_status(self, slug: str, status: str) -> bool:
        """Updates the status of a lead/client by slug across both leads and clients tables."""
        clean_slug = slug.strip().lower()
        now = time.time()
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE leads SET status = ? WHERE slug = ?", (status, clean_slug))
            leads_updated = cur.rowcount > 0
            cur.execute("UPDATE clients SET status = ?, updated_at = ? WHERE slug = ?", (status, now, clean_slug))
            clients_updated = cur.rowcount > 0
            conn.commit()
        if clients_updated:
            client_data = self.get_client(clean_slug)
            if client_data:
                self._sync_to_obsidian(client_data)
        return leads_updated or clients_updated

    def get_client(self, slug: str) -> Optional[Dict[str, Any]]:
        """Retrieve client and their interaction history."""
        clean_slug = slug.strip().lower()
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM clients WHERE slug = ?", (clean_slug,))
            row = cur.fetchone()
            if not row:
                return None

            data = dict(row)
            cur.execute("SELECT * FROM interactions WHERE client_slug = ? ORDER BY timestamp DESC", (clean_slug,))
            data["interactions"] = [dict(r) for r in cur.fetchall()]
            return data

    def list_clients(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all tracked pipeline clients, optionally filtered by status."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            if status:
                cur.execute("SELECT * FROM clients WHERE status = ? ORDER BY updated_at DESC", (status.strip().lower(),))
            else:
                cur.execute("SELECT * FROM clients ORDER BY updated_at DESC")
            return [dict(r) for r in cur.fetchall()]

    def log_interaction(self, client_slug: str, channel: str, direction: str, content: str) -> Dict[str, Any]:
        """Record an interaction and update Obsidian note."""
        clean_slug = client_slug.strip().lower()
        now = time.time()
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO interactions (client_slug, channel, direction, content, timestamp)
                VALUES (?, ?, ?, ?, ?)
            """, (clean_slug, channel.lower(), direction.lower(), content.strip(), now))
            cur.execute("UPDATE clients SET updated_at = ? WHERE slug = ?", (now, clean_slug))
            conn.commit()

        client_data = self.get_client(clean_slug)
        if client_data:
            self._sync_to_obsidian(client_data)
        return {"status": "success", "client": clean_slug, "timestamp": now}

    # =========================================================================
    # Suppression Management
    # =========================================================================

    @staticmethod
    def normalize_suppression_value(kind: str, value: str) -> str:
        """Normalize identifier for suppression lookup."""
        k = kind.strip().lower()
        val = value.strip()
        if k == "email":
            return val.lower()
        elif k == "phone":
            digits = re.sub(r"[^\d]", "", val)
            return digits[-10:] if len(digits) >= 10 else digits
        elif k in ("domain", "website"):
            clean = re.sub(r"^https?://", "", val, flags=re.IGNORECASE)
            clean = clean.split("/")[0].split(":")[0].lower()
            if clean.startswith("www."):
                clean = clean[4:]
            return clean
        elif k in ("maps_cid", "cid"):
            return val
        elif k == "name":
            return re.sub(r"[^\w\s]", "", val).strip().lower()
        return val.lower()

    def add_suppression(
        self,
        kind: str,
        value: str,
        reason: str,
        source: str = "manual"
    ) -> Dict[str, Any]:
        """Add an entry to the suppression list."""
        norm_val = self.normalize_suppression_value(kind, value)
        supp_id = f"supp_{uuid.uuid4().hex[:12]}"
        now = time.time()
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO suppression (id, kind, value_normalised, reason, added_at, source)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (supp_id, kind.strip().lower(), norm_val, reason.strip(), now, source))
            conn.commit()

        return {
            "id": supp_id,
            "kind": kind.strip().lower(),
            "value_normalised": norm_val,
            "reason": reason.strip(),
            "added_at": now,
            "source": source
        }

    def is_suppressed(
        self,
        kind: Optional[str] = None,
        value: Optional[str] = None,
        lead: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Check if an entity or specific identifier is on the suppression list."""
        checks: List[Tuple[str, str]] = []
        if kind and value:
            norm = self.normalize_suppression_value(kind, value)
            if norm:
                checks.append((kind.strip().lower(), norm))

        if lead:
            if lead.get("phone"):
                norm = self.normalize_suppression_value("phone", str(lead["phone"]))
                if norm:
                    checks.append(("phone", norm))
            if lead.get("email"):
                norm = self.normalize_suppression_value("email", str(lead["email"]))
                if norm:
                    checks.append(("email", norm))
            if lead.get("website"):
                norm = self.normalize_suppression_value("domain", str(lead["website"]))
                if norm:
                    checks.append(("domain", norm))
            if lead.get("maps_cid"):
                norm = self.normalize_suppression_value("maps_cid", str(lead["maps_cid"]))
                if norm:
                    checks.append(("maps_cid", norm))
            if lead.get("name"):
                norm = self.normalize_suppression_value("name", str(lead["name"]))
                if norm:
                    checks.append(("name", norm))

        if not checks:
            return False

        with self._get_connection() as conn:
            cur = conn.cursor()
            for k, norm_v in checks:
                cur.execute(
                    "SELECT id FROM suppression WHERE kind = ? AND value_normalised = ?",
                    (k, norm_v)
                )
                if cur.fetchone():
                    return True
        return False

    def list_suppressions(self) -> List[Dict[str, Any]]:
        """List all suppression records."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM suppression ORDER BY added_at DESC")
            return [dict(r) for r in cur.fetchall()]

    # =========================================================================
    # Events & Audit Logging
    # =========================================================================

    def record_event(
        self,
        event_type: str,
        lead_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Record an audit trail event."""
        eid = f"evt_{uuid.uuid4().hex[:12]}"
        now = time.time()
        payload_str = json.dumps(payload or {})
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO events (id, lead_id, type, payload_json, at)
                VALUES (?, ?, ?, ?, ?)
            """, (eid, lead_id, event_type.strip(), payload_str, now))
            conn.commit()

        return {"id": eid, "lead_id": lead_id, "type": event_type, "payload": payload or {}, "at": now}

    def get_events(self, lead_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """Fetch audit events, optionally for a specific lead."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            if lead_id:
                cur.execute("SELECT * FROM events WHERE lead_id = ? ORDER BY at DESC LIMIT ?", (lead_id.strip(), limit))
            else:
                cur.execute("SELECT * FROM events ORDER BY at DESC LIMIT ?", (limit,))
            rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                try:
                    r["payload"] = json.loads(r.get("payload_json") or "{}")
                except Exception:
                    r["payload"] = {}
            return rows

    def has_processed_event(self, event_id: str) -> bool:
        """Check if an external webhook event ID has already been processed."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM processed_events WHERE event_id = ?", (event_id.strip(),))
            return cur.fetchone() is not None

    def record_processed_event(self, event_id: str, provider: str = "razorpay") -> bool:
        """Record an external webhook event ID in the database for deduplication."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT OR IGNORE INTO processed_events (event_id, provider, processed_at) VALUES (?, ?, ?)",
                (event_id.strip(), provider.strip().lower(), time.time())
            )
            conn.commit()
            return cur.rowcount > 0

    # =========================================================================
    # Drafts & Documents (Part 4 & 5 readiness)
    # =========================================================================

    def add_or_update_draft(
        self,
        lead_id: str,
        channel: str,
        body: str,
        subject: Optional[str] = None,
        link: Optional[str] = None,
        status: str = "draft",
        draft_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Insert or update an outbound draft."""
        did = draft_id or f"draft_{uuid.uuid4().hex[:12]}"
        now = time.time()
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM drafts WHERE id = ?", (did,))
            if cur.fetchone():
                cur.execute("""
                    UPDATE drafts SET
                        channel = ?, subject = ?, body = ?, link = ?, status = ?
                    WHERE id = ?
                """, (channel, subject, body, link, status, did))
            else:
                cur.execute("""
                    INSERT INTO drafts (id, lead_id, channel, subject, body, link, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (did, lead_id, channel, subject, body, link, status, now))
            conn.commit()
        return {"id": did, "lead_id": lead_id, "channel": channel, "subject": subject, "body": body, "link": link, "status": status}

    def get_drafts_for_lead(self, lead_id: str) -> List[Dict[str, Any]]:
        """Retrieve all drafts for a lead."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM drafts WHERE lead_id = ? ORDER BY created_at DESC", (lead_id.strip(),))
            return [dict(r) for r in cur.fetchall()]

    def get_draft(self, draft_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a single draft by ID."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM drafts WHERE id = ?", (draft_id.strip(),))
            row = cur.fetchone()
            return dict(row) if row else None

    def get_daily_sends_count(self) -> int:
        """Count drafts marked as sent today (last 24 hours)."""
        now = time.time()
        start_of_day = now - (now % 86400)
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM drafts WHERE status = 'sent' AND sent_at >= ?", (start_of_day,))
            row = cur.fetchone()
            return row[0] if row else 0

    def add_document(
        self,
        lead_id: str,
        kind: str,
        path: str,
        version: int = 1,
        status: str = "draft",
        doc_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Record a generated document (contract, welcome, invoice)."""
        doc_id = doc_id or f"doc_{uuid.uuid4().hex[:12]}"
        now = time.time()
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO documents (id, lead_id, kind, version, path, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (doc_id, lead_id, kind, version, path, status, now))
            conn.commit()
        return {"id": doc_id, "lead_id": lead_id, "kind": kind, "path": path, "status": status}

    def get_documents_for_lead(self, lead_id: str) -> List[Dict[str, Any]]:
        """Retrieve all documents for a lead."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM documents WHERE lead_id = ? ORDER BY created_at DESC", (lead_id.strip(),))
            return [dict(r) for r in cur.fetchall()]

    def update_document_status(self, doc_id: str, status: str) -> bool:
        """Update the status of a document (e.g. draft, lawyer_reviewed, sent, signed)."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE documents SET status = ? WHERE id = ?", (status.strip(), doc_id.strip()))
            conn.commit()
            return cur.rowcount > 0

    # =========================================================================
    # Obsidian Vault Sync
    # =========================================================================

    def _sync_to_obsidian(self, client: Dict[str, Any]):
        """Format client record into Obsidian markdown note with YAML frontmatter."""
        slug = client["slug"]
        md_path = os.path.join(OBSIDIAN_CLIENTS_DIR, f"{slug}.md")

        created_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(client.get("created_at", time.time())))
        updated_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(client.get("updated_at", time.time())))

        interactions = client.get("interactions", [])
        interaction_rows = []
        for i in interactions:
            t_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(i.get("timestamp", time.time())))
            interaction_rows.append(f"| {t_str} | {i.get('channel', '').upper()} | {i.get('direction', '').title()} | {i.get('content', '')} |")

        table_content = "\n".join(interaction_rows) if interaction_rows else "| *No interactions logged yet* | | | |"

        deal_val = client.get("deal_value")
        deal_val_display = f"${deal_val:,.2f}" if (deal_val is not None and deal_val > 0) else "Unset"
        content = f"""---
slug: "{client.get('slug')}"
business_name: "{client.get('business_name')}"
contact_name: "{client.get('contact_name') or ''}"
email: "{client.get('email') or ''}"
phone: "{client.get('phone') or ''}"
preferred_channel: "{client.get('preferred_channel') or 'email'}"
status: "{client.get('status') or 'unscouted'}"
deal_value: {deal_val if deal_val is not None else 0.0}
created: "{created_str}"
updated: "{updated_str}"
tags:
  - client
  - crm
  - pipeline
---

# {client.get('business_name')}

## Client Metadata
- **Slug**: `{client.get('slug')}`
- **Key Contact**: {client.get('contact_name') or 'N/A'}
- **Email**: {client.get('email') or 'N/A'}
- **Phone**: {client.get('phone') or 'N/A'}
- **Preferred Channel**: `{client.get('preferred_channel')}`
- **Pipeline Stage**: **{client.get('status')}**
- **Deal Value**: **{deal_val_display}**

## Recon Notes & Strategic Brief
{client.get('notes') or 'No recon notes recorded.'}

## Interaction History
| Timestamp | Channel | Direction | Summary |
| :--- | :--- | :--- | :--- |
{table_content}
"""
        try:
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(content)
            logger.info(f"Synced CRM client '{slug}' to Obsidian: {md_path}")
        except Exception as e:
            logger.error(f"Failed syncing client '{slug}' to Obsidian: {e}")

    # ------------------------------------------------------------ telemetry
    # Read-only aggregates the autonomy scheduler needs (Roadmap §1.1 snapshot).
    # Fail-closed philosophy lives in TelemetryProvider; these return zeros on
    # empty data, never raise on missing rows.

    def pipeline_counts(self) -> dict:
        """Counts + values for the decision engine snapshot:
        open_complaints, hot_leads (mid-pipeline leads), replies_waiting,
        pipeline_value_inr, last_closed_deal_age_h."""
        now = time.time()
        with self._get_connection() as conn:
            cur = conn.cursor()

            def _count(sql: str, args: tuple = ()) -> int:
                try:
                    row = cur.execute(sql, args).fetchone()
                    return int(row[0]) if row else 0
                except sqlite3.Error:
                    return 0

            # Complaints: interactions flagged by the reply classifier in the
            # last 7 days whose content begins with the ANGRY verdict tag.
            week_ago = now - 7 * 86400
            open_complaints = _count(
                """SELECT COUNT(DISTINCT client_slug) FROM interactions
                   WHERE direction='inbound' AND content LIKE '[ANGRY]%'
                     AND timestamp >= ?""", (week_ago,))

            hot_leads = _count(
                """SELECT COUNT(*) FROM leads WHERE status IN (
                     'built','deployed','drafts_ready','approved','sent',
                     'replied','agreed','contract_drafted','contract_sent')""") + _count(
                """SELECT COUNT(*) FROM clients WHERE status IN (
                     'agreed','contract_drafted','contract_sent','deposit_received')""")

            replies_waiting = _count(
                """SELECT COUNT(DISTINCT client_slug) FROM interactions
                   WHERE direction='inbound' AND timestamp >= ?
                     AND content LIKE '[%' AND content NOT LIKE '[BOUNCE]%'
                     AND content NOT LIKE '[AUTO_REPLY]%'""", (week_ago,))

            pipeline_value = 0.0
            try:
                row = cur.execute(
                    """SELECT COALESCE(SUM(deal_value), 0) FROM clients
                       WHERE status IN ('agreed','contract_sent','deposit_received')""").fetchone()
                pipeline_value = float(row[0] or 0.0)
            except sqlite3.Error:
                pipeline_value = 0.0

            last_closed = None
            try:
                row = cur.execute(
                    """SELECT MAX(updated_at) FROM clients
                       WHERE status = 'won'""").fetchone()
                if row and row[0]:
                    last_closed = round((now - float(row[0])) / 3600.0, 2)
            except sqlite3.Error:
                last_closed = None

        return {
            "open_complaints": open_complaints,
            "hot_leads": hot_leads,
            "replies_waiting": replies_waiting,
            "pipeline_value_inr": pipeline_value,
            "last_closed_deal_age_h": last_closed,
        }

    def quota_remaining_pct(self) -> float:
        """Token-budget remaining for the day, from token_tracker's stats file.
        Unreadable -> 0.0 (fail closed: no quota knowledge = no hunting)."""
        try:
            stats_path = os.environ.get("GHOST_TOKEN_STATS_PATH") or os.path.join(
                REPO_DIR, "configs", "token_stats.json")
            with open(stats_path, "r", encoding="utf-8") as f:
                stats = json.load(f)
            remaining = stats.get("quota_remaining_pct")
            if remaining is None:
                return 0.0
            return max(0.0, min(100.0, float(remaining)))
        except Exception:
            return 0.0
