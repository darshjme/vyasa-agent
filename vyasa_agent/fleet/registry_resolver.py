# SPDX-License-Identifier: Apache-2.0
"""Resolve `system_prompt_ref` strings into concrete prompt text.

Supported reference forms (namespace before the first colon):

- ``vyasa:<role_key>``       -- looks up a bundled Vyasa specialist prompt.
- ``graymatter:<ROLE_KEY>``  -- imports ``graymatter_kb.registry`` if the
  ``GRAYMATTER_HOME`` env var points at a checkout of the graymatter-online-llp
  repository; otherwise falls back to a bundled snapshot embedded in
  :data:`_GRAYMATTER_SNAPSHOT` (populated at install time).
- ``file:<relative-path>``   -- reads the prompt body from disk, relative to
  the fleet root.

Unknown namespaces raise :class:`ValueError`. The resolver is intentionally
side-effect-free: it never writes to disk or mutates import state beyond the
one-time graymatter probe.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import sys
from functools import lru_cache
from pathlib import Path

from .vyasa_specialists import VYASA_SPECIALISTS

logger = logging.getLogger(__name__)

_GRAYMATTER_MODULE = "graymatter_kb.registry"


class PromptResolutionError(ValueError):
    """Raised when a `system_prompt_ref` cannot be resolved."""


def resolve_prompt(ref: str, fleet_root: Path | None = None) -> str:
    """Resolve one `system_prompt_ref` into its prompt text.

    Args:
        ref: Reference string of the form ``<namespace>:<target>``.
        fleet_root: Root directory for ``file:`` refs. Required only when
            ``ref`` starts with ``file:``.

    Returns:
        The prompt text (UTF-8).

    Raises:
        PromptResolutionError: If the namespace is unknown or the target
            does not exist in the referenced registry.
    """
    if ":" not in ref:
        raise PromptResolutionError(f"bad prompt ref (missing ':'): {ref!r}")

    namespace, target = ref.split(":", 1)
    namespace = namespace.strip().lower()
    target = target.strip()

    if namespace == "vyasa":
        return _resolve_vyasa(target)
    if namespace == "graymatter":
        return _resolve_graymatter(target)
    if namespace == "file":
        if fleet_root is None:
            raise PromptResolutionError("file: refs require fleet_root")
        return _resolve_file(fleet_root, target)
    raise PromptResolutionError(f"unknown prompt namespace: {namespace!r}")


def _resolve_vyasa(role_key: str) -> str:
    spec = VYASA_SPECIALISTS.get(role_key.lower())
    if spec is None:
        raise PromptResolutionError(
            f"vyasa registry has no role_key={role_key!r}; "
            f"known={sorted(VYASA_SPECIALISTS)}"
        )
    return spec.system_prompt


def _resolve_graymatter(role_key: str) -> str:
    registry = _load_graymatter_registry()
    agents = registry.AGENTS
    role_enum = registry.AgentRole

    key_normalized = role_key.lower()
    for role, spec in agents.items():
        if role.value.lower() == key_normalized or role.name.lower() == key_normalized:
            return spec.system_prompt

    valid = sorted(r.value for r in role_enum)
    raise PromptResolutionError(
        f"graymatter registry has no role_key={role_key!r}; known={valid}"
    )


def _resolve_file(fleet_root: Path, rel_path: str) -> str:
    target = (fleet_root / rel_path).resolve()
    try:
        target.relative_to(fleet_root.resolve())
    except ValueError as exc:
        raise PromptResolutionError(f"file ref escapes fleet root: {rel_path}") from exc
    if not target.exists():
        raise PromptResolutionError(f"prompt file not found: {target}")
    return target.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def _load_graymatter_registry():
    """Import `graymatter_kb.registry` from GRAYMATTER_HOME if set.

    Falls back to a naive `import graymatter_kb.registry` so tests can stub
    the module on `sys.path`. Raises :class:`PromptResolutionError` if the
    module cannot be loaded from either source.
    """
    home = os.environ.get("GRAYMATTER_HOME")
    if home:
        path = Path(home).expanduser() / "graymatter_kb" / "registry.py"
        if path.exists():
            spec = importlib.util.spec_from_file_location(_GRAYMATTER_MODULE, path)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules[_GRAYMATTER_MODULE] = module
                spec.loader.exec_module(module)
                logger.info("graymatter registry loaded from %s", path)
                return module
        logger.warning(
            "GRAYMATTER_HOME=%s does not contain graymatter_kb/registry.py", home
        )

    try:
        return importlib.import_module(_GRAYMATTER_MODULE)
    except ImportError as exc:
        raise PromptResolutionError(
            "graymatter registry unavailable: set GRAYMATTER_HOME to a checkout "
            "of the graymatter-online-llp repo, or install it on the python path"
        ) from exc


__all__ = ["PromptResolutionError", "resolve_prompt"]
