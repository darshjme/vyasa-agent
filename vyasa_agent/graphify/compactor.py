"""Graph compaction — v0.1 rules-only pass.

This is the minimum viable compactor described in
design-03-memory.md §7, without the semantic-clustering step. Three
rules are applied:

1. **Dedup by checksum.** ``GraphStore.upsert_node`` already rejects
   duplicate checksums, so the compactor simply counts them for
   reporting.
2. **Supersede collapse.** Any node listed in another active node's
   ``supersedes`` array is archived (it has been replaced).
3. **TTL archive.** Active nodes whose ``ttl_days`` has elapsed since
   ``updated_at`` are archived.

Semantic clustering + generated summarisation land in a follow-up PR
when the vector backend arrives.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from .store import GraphStore
from .types import CompactionReport


def _parse_iso(value: str) -> datetime | None:
    try:
        cleaned = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(cleaned)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed
    except ValueError:
        return None


class Compactor:
    """Rules-only compactor. Stateless; holds no connection of its own."""

    async def compact(
        self,
        trigger: Literal["count", "time"],
        store: GraphStore,
    ) -> CompactionReport:
        """Run one compaction pass against ``store`` and return the report."""

        report = CompactionReport(trigger=trigger)

        # Pull active nodes. The active-set is the only candidate pool
        # for archival; already-archived rows are left alone.
        active = await store.iter_active_nodes(batch_size=5000)
        report.scanned = len(active)

        # --- rule 1: checksum dedup --------------------------------- #
        # The store's upsert path enforces uniqueness; here we just
        # double-check that the index is intact and note any phantom
        # duplicates for the report.
        seen_checksums: set[str] = set()
        duplicate_ids: list[str] = []
        for node in active:
            if not node.checksum:
                continue
            if node.checksum in seen_checksums:
                duplicate_ids.append(node.id)
            else:
                seen_checksums.add(node.checksum)
        if duplicate_ids:
            report.notes.append(
                f"found {len(duplicate_ids)} phantom duplicates — "
                f"schema UNIQUE index should have prevented these"
            )
        report.deduped = len(duplicate_ids)

        # --- rule 2: supersede collapse ----------------------------- #
        superseded: set[str] = set()
        for node in active:
            for old_id in node.supersedes or []:
                if old_id and old_id != node.id:
                    superseded.add(old_id)

        now_iso = datetime.now(UTC).isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        )
        if superseded:
            collapsed = await store.mark_archived(sorted(superseded), now_iso)
            report.superseded_collapsed = collapsed

        # --- rule 3: TTL archive ------------------------------------ #
        now = datetime.now(UTC)
        ttl_victims: list[str] = []
        for node in active:
            if node.ttl_days is None:
                continue
            updated = _parse_iso(node.updated_at)
            if updated is None:
                continue
            if now - updated >= timedelta(days=node.ttl_days):
                ttl_victims.append(node.id)

        if ttl_victims:
            archived_count = await store.mark_archived(ttl_victims, now_iso)
            report.archived_ttl = archived_count

        report.finished_at = now_iso
        return report


__all__ = ["Compactor"]
