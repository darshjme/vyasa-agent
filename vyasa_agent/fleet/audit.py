"""Layer C — append-only audit sink for capability decisions.

Every tool invocation (allow, deny, approve, expire) appends one
:class:`AuditRecord` to a daily JSONL file at
``~/.vyasa/audit/audit-YYYY-MM-DD.jsonl`` and mirrors it into a SQLite
table ``tool_audit`` in ``~/.vyasa/audit/tool_audit.sqlite``. The double
write lets operators tail text logs during an incident while compliance
readers run SQL against the mirror.

Rotation runs nightly: files older than 90 days move to cold storage
(``~/.vyasa/audit/cold/``) and are expired after 1 year. The cold-move
is deliberately filesystem-local; the ops team syncs that directory to
S3 Glacier out-of-band.

No hermes imports. All I/O runs through :mod:`asyncio.to_thread` so the
sink is safe to call from async hook handlers.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .capability import Decision

logger = logging.getLogger(__name__)

DEFAULT_AUDIT_ROOT = Path.home() / ".vyasa" / "audit"
HOT_RETENTION_DAYS = 90
COLD_RETENTION_DAYS = 365


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


class AuditRecord(BaseModel):
    """One audit entry covering a single tool invocation decision."""

    model_config = ConfigDict(extra="forbid")

    timestamp: datetime = Field(default_factory=_utcnow)
    employee_id: str
    tool_name: str
    decision: Decision
    args_hash: str = Field(..., description="sha256 hex of the canonical args JSON")
    duration_ms: int = Field(0, ge=0)
    trace_id: str
    rationale: str | None = None
    result_summary: str | None = Field(default=None, max_length=512)

    def to_jsonl(self) -> str:
        """Render as one canonical JSON line (sorted keys, no trailing NL)."""
        payload = self.model_dump(mode="json")
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def hash_args(args: dict[str, Any]) -> str:
        """Stable sha256 over canonical-json args — never stores plaintext."""
        canon = json.dumps(args or {}, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tool_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    NOT NULL,
    employee_id TEXT    NOT NULL,
    tool_name   TEXT    NOT NULL,
    decision    TEXT    NOT NULL,
    args_hash   TEXT    NOT NULL,
    duration_ms INTEGER NOT NULL,
    trace_id    TEXT    NOT NULL,
    rationale   TEXT,
    result_summary TEXT
);
CREATE INDEX IF NOT EXISTS idx_tool_audit_employee ON tool_audit(employee_id);
CREATE INDEX IF NOT EXISTS idx_tool_audit_ts       ON tool_audit(timestamp);
"""


class AuditSink:
    """Async JSONL + SQLite double-writer.

    Construction is cheap; no I/O happens until the first :meth:`append`.
    The sink is safe to share across actors — both writers use a single
    :class:`asyncio.Lock` so the JSONL newline framing stays intact.
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root else DEFAULT_AUDIT_ROOT
        self._lock = asyncio.Lock()
        self._sqlite_inited = False

    # ------------------------------ paths -------------------------------

    def _jsonl_path(self, record: AuditRecord) -> Path:
        day = record.timestamp.astimezone(UTC).date().isoformat()
        return self.root / f"audit-{day}.jsonl"

    def _sqlite_path(self) -> Path:
        return self.root / "tool_audit.sqlite"

    def _cold_dir(self) -> Path:
        return self.root / "cold"

    # ---------------------------- sync helpers --------------------------

    def _ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def _ensure_sqlite(self) -> None:
        if self._sqlite_inited:
            return
        self._ensure_root()
        with sqlite3.connect(self._sqlite_path()) as conn:
            conn.executescript(_SCHEMA_SQL)
        self._sqlite_inited = True

    def _write_jsonl(self, path: Path, line: str) -> None:
        self._ensure_root()
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def _write_sqlite(self, record: AuditRecord) -> None:
        self._ensure_sqlite()
        with sqlite3.connect(self._sqlite_path()) as conn:
            conn.execute(
                """
                INSERT INTO tool_audit (
                    timestamp, employee_id, tool_name, decision, args_hash,
                    duration_ms, trace_id, rationale, result_summary
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.timestamp.isoformat(),
                    record.employee_id,
                    record.tool_name,
                    record.decision.value,
                    record.args_hash,
                    record.duration_ms,
                    record.trace_id,
                    record.rationale,
                    record.result_summary,
                ),
            )
            conn.commit()

    # ------------------------------- API --------------------------------

    async def append(self, record: AuditRecord | dict) -> None:
        """Append one record. Accepts a :class:`AuditRecord` or its dict form."""
        if isinstance(record, dict):
            record = AuditRecord.model_validate(record)

        line = record.to_jsonl()
        path = self._jsonl_path(record)

        async with self._lock:
            await asyncio.to_thread(self._write_jsonl, path, line)
            await asyncio.to_thread(self._write_sqlite, record)

    async def rotate(self, *, now: datetime | None = None) -> None:
        """Archive JSONL files older than 90 days, expire > 1 year.

        Intended to be invoked by a nightly routine. Idempotent; runs over
        the directory listing under a lock so it never races ``append``.
        """
        now = now or _utcnow()
        hot_cutoff = now - timedelta(days=HOT_RETENTION_DAYS)
        cold_cutoff = now - timedelta(days=COLD_RETENTION_DAYS)

        async with self._lock:
            await asyncio.to_thread(self._rotate_sync, hot_cutoff, cold_cutoff)

    def _rotate_sync(self, hot_cutoff: datetime, cold_cutoff: datetime) -> None:
        self._ensure_root()
        cold = self._cold_dir()
        cold.mkdir(parents=True, exist_ok=True)

        for path in sorted(self.root.glob("audit-*.jsonl")):
            try:
                day = datetime.strptime(path.stem, "audit-%Y-%m-%d").replace(
                    tzinfo=UTC
                )
            except ValueError:
                logger.warning("skipping malformed audit filename: %s", path.name)
                continue
            if day < hot_cutoff:
                target = cold / path.name
                shutil.move(str(path), str(target))
                logger.info("rotated audit log to cold storage: %s", target)

        for path in sorted(cold.glob("audit-*.jsonl")):
            try:
                day = datetime.strptime(path.stem, "audit-%Y-%m-%d").replace(
                    tzinfo=UTC
                )
            except ValueError:
                continue
            if day < cold_cutoff:
                path.unlink()
                logger.info("expired cold audit log: %s", path)


__all__ = [
    "COLD_RETENTION_DAYS",
    "DEFAULT_AUDIT_ROOT",
    "HOT_RETENTION_DAYS",
    "AuditRecord",
    "AuditSink",
]
