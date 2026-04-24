"""Deterministic checksum helper for Graphify nodes.

The canonical form is ``sha256:<64-hex>``. The pre-hash input is a JSON
document built from ``source_path`` (coerced to an empty string when
absent) plus ``key_claims`` sorted lexicographically, serialised with
stable separators so the bytes are identical across platforms.
"""

from __future__ import annotations

import hashlib
import json

from .types import Node


def compute_checksum(node: Node) -> str:
    """Return the deterministic checksum for ``node``.

    Parameters
    ----------
    node:
        Any :class:`Node`; fields other than ``source_path`` and
        ``key_claims`` are deliberately ignored so cosmetic edits
        (e.g. ``updated_by`` or ``confidence_score``) do not break
        dedup.
    """

    payload = {
        "source_path": node.source_path or "",
        "key_claims": sorted(node.key_claims),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


__all__ = ["compute_checksum"]
