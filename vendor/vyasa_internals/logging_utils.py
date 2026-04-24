"""Minimal logging utilities for the vendored agent runtime.

Trimmed from the upstream donor's 390-line setup module. Phase-1 Duo only
needs:

* ``set_session_context(session_id)`` / ``clear_session_context()`` so log
  lines can be correlated to a conversation.
* ``setup_logging()`` to route INFO-and-above to ``<vyasa_home>/logs/agent.log``
  with rotation. Gateway/managed/profile layering is intentionally omitted.
* ``setup_verbose_logging()`` for the ``--verbose`` CLI flag.

Heavier features (component filters, redacting formatter, managed chmod
handling) are not wired in this alpha. The interface surface that the rest
of the runtime calls into is preserved so downstream callers keep working.
"""

from __future__ import annotations

import logging
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from vyasa_internals.constants import get_vyasa_home

_logging_initialized = False
_session_context = threading.local()

_LOG_FORMAT = "%(asctime)s %(levelname)s%(session_tag)s %(name)s: %(message)s"
_LOG_FORMAT_VERBOSE = "%(asctime)s - %(name)s - %(levelname)s%(session_tag)s - %(message)s"

_NOISY_LOGGERS = (
    "httpx",
    "httpcore",
    "asyncio",
    "urllib3",
    "urllib3.connectionpool",
    "charset_normalizer",
)


def set_session_context(session_id: str) -> None:
    """Bind ``session_id`` to the current thread for log correlation."""
    _session_context.session_id = session_id


def clear_session_context() -> None:
    """Drop the session id bound to the current thread."""
    _session_context.session_id = None


def _install_session_record_factory() -> None:
    current_factory = logging.getLogRecordFactory()
    if getattr(current_factory, "_vyasa_session_injector", False):
        return

    def _session_record_factory(*args, **kwargs):
        record = current_factory(*args, **kwargs)
        sid = getattr(_session_context, "session_id", None)
        record.session_tag = f" [{sid}]" if sid else ""  # type: ignore[attr-defined]
        return record

    _session_record_factory._vyasa_session_injector = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(_session_record_factory)


_install_session_record_factory()


def setup_logging(
    *,
    vyasa_home: Optional[Path] = None,
    log_level: Optional[str] = None,
    max_size_mb: Optional[int] = None,
    backup_count: Optional[int] = None,
    mode: Optional[str] = None,
    force: bool = False,
) -> Path:
    """Attach a rotating file handler for ``agent.log`` under ``vyasa_home``.

    Returns the logs directory. Safe to call multiple times — subsequent
    calls are no-ops unless ``force`` is True.
    """
    global _logging_initialized

    home = vyasa_home or get_vyasa_home()
    log_dir = home / "logs"

    if _logging_initialized and not force:
        return log_dir

    log_dir.mkdir(parents=True, exist_ok=True)

    level_name = (log_level or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    max_bytes = (max_size_mb or 5) * 1024 * 1024
    backups = backup_count or 3

    root = logging.getLogger()

    handler = RotatingFileHandler(
        str(log_dir / "agent.log"),
        maxBytes=max_bytes,
        backupCount=backups,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    root.addHandler(handler)

    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)

    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    _logging_initialized = True
    return log_dir


def setup_verbose_logging() -> None:
    """Attach a DEBUG stream handler for ``--verbose`` CLI mode."""
    root = logging.getLogger()

    for handler in root.handlers:
        if isinstance(handler, logging.StreamHandler) and getattr(handler, "_vyasa_verbose", False):
            return

    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT_VERBOSE, datefmt="%H:%M:%S"))
    handler._vyasa_verbose = True  # type: ignore[attr-defined]
    root.addHandler(handler)

    if root.level > logging.DEBUG:
        root.setLevel(logging.DEBUG)

    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
