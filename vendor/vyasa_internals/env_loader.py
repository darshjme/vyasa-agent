"""Stub .env loader used by the vendored agent runtime.

The upstream donor shipped a richer dotenv loader that layered ``~/.vyasa/.env``
on top of the project-root ``.env`` with strict precedence semantics. For
Phase-1 Duo we only need the precedence order, not the fancy reporting.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional


def load_vyasa_dotenv(
    *,
    vyasa_home: Optional[Path] = None,
    project_env: Optional[Path] = None,
) -> List[Path]:
    """Load environment variables from VYASA_HOME/.env then project_env.

    Values set in the shell take precedence. Values from ``vyasa_home/.env``
    override values from ``project_env``. Missing ``python-dotenv`` is
    tolerated — the function becomes a no-op that returns an empty list.

    Returns the list of ``.env`` paths that were actually loaded, in the
    order they were applied.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return []

    loaded: List[Path] = []
    candidates: List[Path] = []
    if project_env is not None:
        candidates.append(Path(project_env))
    if vyasa_home is not None:
        candidates.append(Path(vyasa_home) / ".env")

    for path in candidates:
        if path.is_file():
            load_dotenv(path, override=False)
            loaded.append(path)

    return loaded
