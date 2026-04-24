"""Stub provider-timeout resolver used by the vendored agent runtime.

The upstream donor read nested ``timeouts.*`` blocks from ``config.yaml``.
For Phase-1 Duo we fall back to sensible defaults and honour two env
variables, which is sufficient to keep the runtime importable.
"""

from __future__ import annotations

import os

DEFAULT_REQUEST_TIMEOUT_SECONDS = 300.0
DEFAULT_STALE_TIMEOUT_SECONDS = 600.0


def _read_float(env_name: str, default: float) -> float:
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def get_provider_request_timeout(provider: str | None = None) -> float:
    """Return the per-request timeout in seconds (default 300s)."""
    return _read_float("VYASA_REQUEST_TIMEOUT", DEFAULT_REQUEST_TIMEOUT_SECONDS)


def get_provider_stale_timeout(provider: str | None = None) -> float:
    """Return the stale-stream timeout in seconds (default 600s)."""
    return _read_float("VYASA_STALE_TIMEOUT", DEFAULT_STALE_TIMEOUT_SECONDS)
