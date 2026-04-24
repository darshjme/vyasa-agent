"""Helpers that keep :mod:`vyasa_agent.cli` short and testable.

This module centralises the bits of CLI plumbing that would otherwise bloat
``cli.py``: pretty-print helpers built on :mod:`rich`, structured JSON logging
for headless mode, an in-process uvicorn launcher, and a graceful-shutdown
coordinator that installs SIGINT/SIGTERM handlers on the running event loop.

Nothing here performs network I/O by itself. Callers are expected to boot
the fleet, adapters, and uvicorn server explicitly; this module only wires
signal handling, log formatting, and rendering.
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from rich.console import Console
from rich.table import Table

__all__ = [
    "DEFAULT_SHUTDOWN_TIMEOUT_S",
    "GracefulShutdown",
    "ShutdownRequested",
    "configure_logging",
    "employee_table",
    "is_tty",
    "render_doctor_report",
    "render_graph_nodes",
    "run_uvicorn_in_thread",
]

DEFAULT_SHUTDOWN_TIMEOUT_S: float = 30.0


# --------------------------------------------------------------------------- #
# console / tty detection
# --------------------------------------------------------------------------- #


def is_tty() -> bool:
    """Return ``True`` when stdout is attached to a terminal."""
    try:
        return bool(sys.stdout.isatty())
    except (AttributeError, ValueError):
        return False


def _stdout_console() -> Console:
    return Console(soft_wrap=False)


def _stderr_console() -> Console:
    return Console(file=sys.stderr, soft_wrap=False)


# --------------------------------------------------------------------------- #
# logging
# --------------------------------------------------------------------------- #


class _JsonLineFormatter(logging.Formatter):
    """Emit every log record as a one-line JSON object on stderr."""

    _EXCLUDED = {
        "args", "msg", "levelno", "pathname", "filename", "module", "exc_info",
        "exc_text", "stack_info", "lineno", "funcName", "created", "msecs",
        "relativeCreated", "thread", "threadName", "processName", "process",
        "name", "levelname", "message",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extras = {
            k: v for k, v in record.__dict__.items()
            if k not in self._EXCLUDED and not k.startswith("_")
        }
        if extras:
            payload.update(extras)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(*, headless: bool, level: int = logging.INFO) -> None:
    """Install a single stderr handler using JSON (headless) or rich format."""
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    handler: logging.Handler
    if headless:
        handler = logging.StreamHandler(stream=sys.stderr)
        handler.setFormatter(_JsonLineFormatter())
    else:
        from rich.logging import RichHandler

        handler = RichHandler(
            console=_stderr_console(),
            show_path=False,
            rich_tracebacks=True,
            markup=False,
        )
    handler.setLevel(level)
    root.addHandler(handler)
    root.setLevel(level)


# --------------------------------------------------------------------------- #
# pretty printing
# --------------------------------------------------------------------------- #


def employee_table(rows: Sequence[dict[str, Any]], *, title: str) -> Table:
    """Build a rich table for ``vyasa employee list``."""
    table = Table(title=title, header_style="bold cyan", show_lines=False)
    table.add_column("id", style="bold")
    table.add_column("display_name")
    table.add_column("role", style="magenta")
    table.add_column("model")
    table.add_column("enabled", justify="center")
    for row in rows:
        enabled_mark = "[green]yes[/green]" if row.get("enabled", True) else "[red]no[/red]"
        table.add_row(
            str(row.get("id", "")),
            str(row.get("display_name", "")),
            str(row.get("role_key", "")),
            str(row.get("model", "")),
            enabled_mark,
        )
    return table


def render_graph_nodes(nodes: Sequence[Any]) -> Table:
    """Pretty-print a list of graph nodes (duck-typed: id/summary/... attrs)."""
    table = Table(title=f"graph nodes ({len(nodes)})", header_style="bold cyan")
    table.add_column("id", style="bold")
    table.add_column("type", style="magenta")
    table.add_column("visibility")
    table.add_column("owner")
    table.add_column("summary")
    for node in nodes:
        summary = str(getattr(node, "summary", "") or "")
        if len(summary) > 80:
            summary = summary[:77] + "..."
        table.add_row(
            str(getattr(node, "id", "")),
            str(getattr(node, "type", "")),
            str(getattr(node, "visibility", "")),
            str(getattr(node, "owner_employee_id", "")),
            summary,
        )
    return table


def render_doctor_report(checks: Sequence[tuple[str, bool, str]]) -> Table:
    """Render the doctor checklist with tick/cross glyphs."""
    table = Table(title="vyasa doctor", header_style="bold cyan")
    table.add_column("check", style="bold")
    table.add_column("status", justify="center")
    table.add_column("detail", overflow="fold")
    for label, ok, detail in checks:
        glyph = "[green]OK[/green]" if ok else "[red]FAIL[/red]"
        table.add_row(label, glyph, detail)
    return table


# --------------------------------------------------------------------------- #
# uvicorn launcher
# --------------------------------------------------------------------------- #


def run_uvicorn_in_thread(
    app: Any,
    *,
    host: str,
    port: int,
    log_level: str = "info",
) -> tuple[Any, Callable[[], None]]:
    """Start uvicorn in a background thread and return ``(server, stop)``.

    The caller is responsible for calling ``stop()`` during shutdown. The
    returned ``server`` is the :class:`uvicorn.Server` instance so callers can
    observe ``server.started`` during integration tests.
    """
    import threading

    import uvicorn

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level=log_level,
        lifespan="on",
        loop="asyncio",
        access_log=False,
    )
    server = uvicorn.Server(config)
    # Prevent uvicorn from installing its own signal handlers; we own them.
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
    thread = threading.Thread(
        target=server.run, name="vyasa-admin-uvicorn", daemon=True
    )
    thread.start()

    def _stop() -> None:
        server.should_exit = True
        thread.join(timeout=10.0)

    return server, _stop


# --------------------------------------------------------------------------- #
# graceful shutdown
# --------------------------------------------------------------------------- #


class ShutdownRequested(Exception):
    """Raised inside the main loop when SIGTERM/SIGINT lands."""


@dataclass
class GracefulShutdown:
    """Install SIGINT/SIGTERM handlers that set an :class:`asyncio.Event`.

    The coordinator is event-loop-bound; construct it inside the loop.
    """

    event: asyncio.Event = field(default_factory=asyncio.Event)
    _installed: bool = False

    def install(self) -> None:
        if self._installed:
            return
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._trip)
            except NotImplementedError:
                # Windows: loop.add_signal_handler is not supported.
                # Fall back to signal.signal for the signals Windows
                # actually delivers (SIGINT, SIGBREAK). SIGTERM on
                # Windows raises ValueError because only SIGINT/SIGBREAK
                # are supported in the console handler; guard so the
                # install path stays quiet on that platform.
                try:
                    signal.signal(sig, lambda *_: self._trip())
                except (ValueError, OSError):
                    continue
        self._installed = True

    def _trip(self) -> None:
        self.event.set()

    async def wait(self) -> None:
        await self.event.wait()


# --------------------------------------------------------------------------- #
# path helpers
# --------------------------------------------------------------------------- #


def repo_root() -> Path:
    """Return the repository root (parent of this file's package directory)."""
    return Path(__file__).resolve().parent.parent
