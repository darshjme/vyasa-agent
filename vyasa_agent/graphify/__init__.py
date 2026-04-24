"""Graphify v2 — SQLite-backed fleet memory fabric.

Public surface:

* :class:`GraphStore` — WAL-mode SQLite store (see ``store.py``).
* :class:`Compactor`  — rules-only maintenance pass (see ``compactor.py``).
* :class:`PIIScrubber` — pre-write PII gate (see ``pii.py``).
* Pydantic models: :class:`Node`, :class:`Edge`, :class:`Episode`,
  :class:`QueryFilters`, :class:`Subgraph`.
* :func:`compute_checksum` — deterministic node checksum.
"""

from .checksum import compute_checksum
from .compactor import Compactor
from .pii import PIIScrubber
from .store import GraphStore, default_graph_path
from .types import (
    CompactionReport,
    Edge,
    EdgeKind,
    Episode,
    Node,
    NodeStatus,
    PIILeakError,
    QueryFilters,
    Subgraph,
    Visibility,
)

__all__ = [
    "CompactionReport",
    "Compactor",
    "Edge",
    "EdgeKind",
    "Episode",
    "GraphStore",
    "Node",
    "NodeStatus",
    "PIILeakError",
    "PIIScrubber",
    "QueryFilters",
    "Subgraph",
    "Visibility",
    "compute_checksum",
    "default_graph_path",
]
