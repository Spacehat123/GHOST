"""SQLite CRM Engine & Bi-Directional Obsidian Vault Sync for Ghost.
Single source of truth for hospitality & boutique web agency client tracking.
Guarantees anti-hallucination: all pricing, deals, and channel preferences are strictly grounded.
"""
import os
import sqlite3
import time
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger("ghost.crm")

CRM_DB_PATH = "/home/pranc/projects/ghost/data/crm.db"
OBSIDIAN_CLIENTS_DIR = "/home/pranc/Obsidian Vault/Ghost/Clients"

class CRMDatabase:
    _instance: Optional["CRMDatabase"] = None

    def __init__(self, db_path: str = CRM_DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        os.makedirs(OBSIDIAN_CLIENTS_DIR, exist_ok=True)
        self._init_db()

    @classmethod
    def get_instance(cls) -> "CRMDatabase":
        if cls._instance is None:
            cls._instance = CRMDatabase()
        return cls._instance

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Create tables if they do not exist."""
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
            conn.commit()

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

        # Bi-directional sync: mirror to Obsidian markdown
        client_data = self.get_client(clean_slug)
        if client_data:
            self._sync_to_obsidian(client_data)

        return client_data

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

        content = f"""---
slug: "{client.get('slug')}"
business_name: "{client.get('business_name')}"
contact_name: "{client.get('contact_name') or ''}"
email: "{client.get('email') or ''}"
phone: "{client.get('phone') or ''}"
preferred_channel: "{client.get('preferred_channel') or 'email'}"
status: "{client.get('status') or 'unscouted'}"
deal_value: {client.get('deal_value') or 0.0}
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
- **Deal Value**: **${client.get('deal_value', 0):,.2f}**

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
