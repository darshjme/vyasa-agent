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

    TODO(Dharma HIGH): two nodes that share ``source_path`` and
    ``key_claims`` but carry different ``summary`` or ``entities`` will
    hash to the same checksum and dedup against each other. That is the
    intended v0.1 behaviour per design-03 §6 (claims are the stable
    identity), but it is not collision-safe against adversarial inputs.
    Consider folding ``summary`` into the canonical payload if the
    semantic-recall layer starts keying on it.
    """

    payload = {
        "source_path": node.source_path or "",
        "key_claims": sorted(node.key_claims),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


__all__ = ["compute_checksum"]
