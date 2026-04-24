"""SQLite-backed settings store (design-06 §5, design-08 §7).

Single table ``settings`` keyed by dotted path (e.g. ``branding.product_name``).
Every row carries a JSON value, a section bucket for UI grouping, an optional
schema ref for form rendering, and the actor/timestamp of the last write.

Intentionally dependency-light: standard-library ``sqlite3`` only. The admin
panel and any boot hook can import this module without dragging in SQLAlchemy.

A parallel ``settings_audit`` log captures before/after snapshots so every
write satisfies design-06 §9 (audit log). Secrets are never redacted here —
callers must pass redacted copies if storing sensitive values.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key          TEXT PRIMARY KEY,
    value_json   TEXT NOT NULL,
    section      TEXT NOT NULL,
    schema_json  TEXT,
    updated_by   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_settings_section ON settings(section);

CREATE TABLE IF NOT EXISTS settings_audit (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    key          TEXT NOT NULL,
    before_json  TEXT,
    after_json   TEXT NOT NULL,
    actor        TEXT NOT NULL,
    at           TEXT NOT NULL
);
"""


def _utcnow_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


class SettingsStore:
    """Thread-safe settings store backed by SQLite.

    All mutating calls hold an internal lock because ``sqlite3`` connections
    are not safe to share across threads by default. Reads use the same lock
    for simplicity; the admin panel's write volume is low enough that this
    does not matter.
    """

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._db_path = str(db_path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self._db_path,
            check_same_thread=False,
            isolation_level=None,  # autocommit; we use explicit transactions
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    # ------------------------------ API ---------------------------------

    def get(self, key: str) -> Any | None:
        """Return the parsed value for ``key`` or ``None`` if absent."""
        with self._lock:
            row = self._conn.execute(
                "SELECT value_json FROM settings WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["value_json"])

    def get_row(self, key: str) -> dict[str, Any] | None:
        """Return the full row dict for UI rendering, or ``None`` if absent."""
        with self._lock:
            row = self._conn.execute(
                "SELECT key, value_json, section, schema_json, updated_by, updated_at "
                "FROM settings WHERE key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        return _row_to_dict(row)

    def set(
        self,
        key: str,
        value: Any,
        user: str,
        *,
        section: str | None = None,
        schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Upsert ``key`` and return the stored row, writing one audit entry."""
        if not key or not isinstance(key, str):
            raise ValueError("settings key must be a non-empty string")
        value_json = json.dumps(value, ensure_ascii=False, sort_keys=True)
        now = _utcnow_iso()

        with self._lock:
            existing = self._conn.execute(
                "SELECT value_json, section, schema_json FROM settings WHERE key = ?",
                (key,),
            ).fetchone()

            resolved_section = section or (existing["section"] if existing else "general")
            resolved_schema = (
                json.dumps(schema, ensure_ascii=False, sort_keys=True)
                if schema is not None
                else (existing["schema_json"] if existing else None)
            )
            before_json = existing["value_json"] if existing else None

            self._conn.execute("BEGIN")
            try:
                self._conn.execute(
                    """
                    INSERT INTO settings (key, value_json, section, schema_json, updated_by, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value_json = excluded.value_json,
                        section = excluded.section,
                        schema_json = excluded.schema_json,
                        updated_by = excluded.updated_by,
                        updated_at = excluded.updated_at
                    """,
                    (key, value_json, resolved_section, resolved_schema, user, now),
                )
                self._conn.execute(
                    "INSERT INTO settings_audit (key, before_json, after_json, actor, at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (key, before_json, value_json, user, now),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

        return {
            "key": key,
            "value_json": value_json,
            "section": resolved_section,
            "schema_json": resolved_schema,
            "updated_by": user,
            "updated_at": now,
        }

    def list(self, section: str | None = None) -> list[dict[str, Any]]:
        """Return all settings rows, optionally filtered by ``section``."""
        with self._lock:
            if section is None:
                rows = self._conn.execute(
                    "SELECT key, value_json, section, schema_json, updated_by, updated_at "
                    "FROM settings ORDER BY section, key"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT key, value_json, section, schema_json, updated_by, updated_at "
                    "FROM settings WHERE section = ? ORDER BY key",
                    (section,),
                ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def list_grouped(self) -> dict[str, list[dict[str, Any]]]:
        """Return all rows grouped by section, for the admin UI index."""
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in self.list():
            grouped.setdefault(row["section"], []).append(row)
        return grouped

    def seed_defaults(self, defaults: list[dict[str, Any]], actor: str = "system") -> int:
        """Insert any missing default settings; return the count inserted."""
        inserted = 0
        for item in defaults:
            key = item["key"]
            if self.get(key) is not None:
                continue
            self.set(
                key,
                item["value"],
                actor,
                section=item.get("section", "general"),
                schema=item.get("schema"),
            )
            inserted += 1
        return inserted

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "key": row["key"],
        "value": json.loads(row["value_json"]),
        "section": row["section"],
        "schema": json.loads(row["schema_json"]) if row["schema_json"] else None,
        "updated_by": row["updated_by"],
        "updated_at": row["updated_at"],
    }


__all__ = ["SettingsStore"]
