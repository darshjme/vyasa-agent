"""Stub for the upstream config module.

The vendored logging utilities reference ``is_managed()`` to decide whether
to chmod rotated log files. Phase-1 Duo never ships under the managed
deployment model, so the stub returns False unconditionally.
"""

from __future__ import annotations


def is_managed() -> bool:
    """Always False for Phase-1 Duo — we never run under the managed daemon."""
    return False
