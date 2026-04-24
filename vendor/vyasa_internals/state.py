"""Session-state interface for the vendored agent runtime.

The upstream donor shipped a ~1600 LoC SQLite store with FTS5 search,
WAL-mode multi-writer coordination, compression chains, title-lineage and
an extensive `SessionDB` surface. Phase-1 Duo mode does not exercise that
machinery — the fleet orchestrator owns its own persistence via DarshJDB.

This module keeps the import path alive and declares a ``SessionDB``
protocol-like shell so the agent runtime can receive a ``session_db=...``
argument without breaking. A light in-memory fallback is provided so
callers that don't supply their own store still have somewhere to write.

When Phase-2 arrives we can replace this shell with either:
  * a re-vendored slice of the upstream SQLite store (≈ 600 LoC trimmed), or
  * a thin adapter over DarshJDB's session collection.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from vyasa_internals.constants import get_vyasa_home

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = get_vyasa_home() / "state.db"


class SessionDB:
    """Minimal session-log interface.

    The shape of this class mirrors the subset of the upstream API the Duo
    runtime touches at startup. Writes are in-memory by default; passing
    ``db_path`` opens a tiny SQLite file so sessions survive restarts.
    Search / ranking / FTS are deliberately omitted — Phase-2 brings them
    back if required.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = Path(db_path) if db_path else None
        self._lock = threading.RLock()
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._messages: Dict[str, List[Dict[str, Any]]] = {}
        self._conn: Optional[sqlite3.Connection] = None
        if self.db_path is not None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(
                str(self.db_path), check_same_thread=False, isolation_level=None
            )
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS vyasa_sessions ("
                "id TEXT PRIMARY KEY, source TEXT, started_at REAL, ended_at REAL, "
                "payload TEXT)"
            )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS vyasa_messages ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, "
                "role TEXT, content TEXT, tool_name TEXT, timestamp REAL)"
            )

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    def create_session(
        self,
        session_id: str,
        source: str,
        user_id: Optional[str] = None,
        model: Optional[str] = None,
        model_config: Optional[Dict[str, Any]] = None,
        system_prompt: Optional[str] = None,
        parent_session_id: Optional[str] = None,
        **_: Any,
    ) -> str:
        now = time.time()
        payload = {
            "user_id": user_id,
            "model": model,
            "model_config": model_config or {},
            "system_prompt": system_prompt,
            "parent_session_id": parent_session_id,
        }
        with self._lock:
            self._sessions[session_id] = {
                "id": session_id,
                "source": source,
                "started_at": now,
                "ended_at": None,
                **payload,
            }
            self._messages.setdefault(session_id, [])
            if self._conn is not None:
                self._conn.execute(
                    "INSERT OR REPLACE INTO vyasa_sessions (id, source, started_at, payload) "
                    "VALUES (?, ?, ?, ?)",
                    (session_id, source, now, json.dumps(payload)),
                )
        return session_id

    def end_session(self, session_id: str, end_reason: str) -> None:
        now = time.time()
        with self._lock:
            row = self._sessions.get(session_id)
            if row is not None:
                row["ended_at"] = now
                row["end_reason"] = end_reason
            if self._conn is not None:
                self._conn.execute(
                    "UPDATE vyasa_sessions SET ended_at=? WHERE id=?",
                    (now, session_id),
                )

    def append_message(
        self,
        session_id: str,
        role: str,
        content: Optional[str] = None,
        tool_call_id: Optional[str] = None,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        tool_name: Optional[str] = None,
        **_: Any,
    ) -> int:
        now = time.time()
        entry: Dict[str, Any] = {
            "role": role,
            "content": content,
            "tool_call_id": tool_call_id,
            "tool_calls": tool_calls,
            "tool_name": tool_name,
            "timestamp": now,
        }
        with self._lock:
            bucket = self._messages.setdefault(session_id, [])
            bucket.append(entry)
            if self._conn is not None:
                self._conn.execute(
                    "INSERT INTO vyasa_messages (session_id, role, content, tool_name, timestamp) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (session_id, role, content or "", tool_name or "", now),
                )
            return len(bucket)

    def get_messages(self, session_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._messages.get(session_id, []))

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._sessions.get(session_id)
            return dict(row) if row is not None else None

    def session_count(self, source: Optional[str] = None) -> int:
        with self._lock:
            if source is None:
                return len(self._sessions)
            return sum(1 for row in self._sessions.values() if row.get("source") == source)

    def message_count(self, session_id: Optional[str] = None) -> int:
        with self._lock:
            if session_id is not None:
                return len(self._messages.get(session_id, []))
            return sum(len(bucket) for bucket in self._messages.values())
