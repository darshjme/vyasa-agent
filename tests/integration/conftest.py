"""Fixtures for the golden smoke-test suite.

All fixtures are constructed so a single test can pull exactly what it
needs (no transitive boot cost for unrelated surfaces).  External
services (chat provider Bot API, inference provider) are always mocked;
``VYASA_STUB_BRIDGE=1`` keeps the actor's ``_execute_turn`` on the
reversed-text stub so tests never reach for a real inference endpoint.
"""

from __future__ import annotations

import asyncio
import io
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

# Keep every integration test on the stub bridge — this prevents any
# accidental outbound request to an inference provider if a parallel
# agent lands real wiring mid-run.
os.environ.setdefault("VYASA_STUB_BRIDGE", "1")

from vyasa_agent.admin_panel.settings_store import SettingsStore
from vyasa_agent.fleet.actor import EmployeeActor
from vyasa_agent.fleet.descriptor import load_fleet
from vyasa_agent.fleet.manager import FleetManager
from vyasa_agent.graphify.store import GraphStore

_REPO_ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- #
# tmp_vyasa_home
# --------------------------------------------------------------------------- #


@pytest.fixture
def tmp_vyasa_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``~/.vyasa/`` (and friends) at a disposable temp dir.

    All state written by the actor (state.db), graph store, settings
    store, and audit sink is constrained to the returned root.  No
    real ``$HOME`` ever gets touched.
    """
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    vyasa_home = home / ".vyasa"
    vyasa_home.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("HOME", str(home))
    # Some libs (paths built via ``Path.home()``) cache HOME through USERPROFILE on
    # Windows — set both so tests are portable to the cross-platform CI.
    monkeypatch.setenv("USERPROFILE", str(home))
    # Graph path override so GraphStore writes into the temp tree regardless
    # of whatever ``Path.home()`` resolves to.
    monkeypatch.setenv("VYASA_GRAPH_PATH", str(vyasa_home / "graph.sqlite"))
    # Audit root under the same tree.
    monkeypatch.setenv("VYASA_AUDIT_ROOT", str(vyasa_home / "audit"))
    return home


# --------------------------------------------------------------------------- #
# in-memory settings store
# --------------------------------------------------------------------------- #


@pytest.fixture
def settings_store_inmem() -> Iterator[SettingsStore]:
    """A zero-config, in-memory :class:`SettingsStore` for tests."""
    store = SettingsStore(":memory:")
    try:
        yield store
    finally:
        try:
            store.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# graph_store_inmem
# --------------------------------------------------------------------------- #


@pytest.fixture
def graph_store_inmem(tmp_vyasa_home: Path) -> Iterator[GraphStore]:
    """A file-backed GraphStore whose file lives under the temp home.

    SQLite in the project uses WAL mode and a schema-bootstrap path that
    prefers a real file over ``:memory:``; a temp-dir file is the most
    faithful "in-memory" stand-in and still cleans up automatically.
    """
    db = tmp_vyasa_home / ".vyasa" / "graph.sqlite"
    store = GraphStore(db_path=db)
    try:
        yield store
    finally:
        async def _close() -> None:
            await store.close()

        try:
            asyncio.get_event_loop().run_until_complete(_close())
        except RuntimeError:
            asyncio.run(_close())


# --------------------------------------------------------------------------- #
# fleet_duo — 2-employee fleet (dr.sarabhai + prometheus)
# --------------------------------------------------------------------------- #


def _yaml_fleet_root(tmp_home: Path) -> Path:
    """Seed a fleet root with the two YAMLs we care about.

    Copies the repo's ``vyasa.yaml`` and the two employee descriptors
    verbatim into the temp tree so the fleet boot path exercises the
    same code that production uses — no private test-only roster.
    """
    fleet_root = tmp_home / "fleet"
    (fleet_root / "employees").mkdir(parents=True, exist_ok=True)

    src_vyasa = _REPO_ROOT / "vyasa.yaml"
    (fleet_root / "vyasa.yaml").write_text(src_vyasa.read_text(), encoding="utf-8")

    for name in ("dr-sarabhai.yaml", "prometheus.yaml"):
        src = _REPO_ROOT / "employees" / name
        (fleet_root / "employees" / name).write_text(src.read_text(), encoding="utf-8")

    return fleet_root


@pytest_asyncio.fixture
async def fleet_duo(tmp_vyasa_home: Path) -> FleetManager:
    """Boot a 2-employee fleet (dr.sarabhai + prometheus) with real YAML.

    The fleet writes its per-employee ``state.db`` under
    ``tmp_vyasa_home/.vyasa/employees/<id>/state.db`` so tests can
    inspect real on-disk artefacts without leaking into the caller's
    home directory.  The manager is shut down at teardown.
    """
    fleet_root = _yaml_fleet_root(tmp_vyasa_home)
    # Ensure the actor state goes under the managed home, not under
    # ``<repo>/employees/state/`` (the manager's default when booting
    # from a root that owns an ``employees/`` directory).
    state_root = tmp_vyasa_home / ".vyasa" / "employees"
    state_root.mkdir(parents=True, exist_ok=True)

    manager = FleetManager()
    # The FleetManager writes state under ``<root>/employees/state/<id>``;
    # since we boot from ``fleet_root`` here, state lands at
    # ``fleet_root/employees/state/<id>/state.db``.  Symlink that back into
    # ``~/.vyasa/employees/<id>/state.db`` so tests can assert against the
    # spec'd location without reaching for manager internals.
    await manager.boot(fleet_root)

    # Mirror the expected ``~/.vyasa/employees/<id>/state.db`` path so
    # tests can assert exactly where the spec says the file lives.
    for emp_id in manager.employee_ids:
        canon = emp_id.replace(".", "-")
        target_dir = tmp_vyasa_home / ".vyasa" / "employees" / canon
        target_dir.mkdir(parents=True, exist_ok=True)

    try:
        yield manager
    finally:
        await manager.shutdown()


# --------------------------------------------------------------------------- #
# mock_telegram — MagicMock python-telegram-bot Bot
# --------------------------------------------------------------------------- #


@pytest.fixture
def mock_telegram() -> SimpleNamespace:
    """Return a drop-in replacement for ``telegram.Bot`` plus convenience hooks.

    The returned namespace exposes:

    * ``bot`` — a ``MagicMock`` with AsyncMock-backed ``send_message``,
      ``edit_message_text``, and ``send_chat_action`` methods.
    * ``sent_texts`` — the ordered list of strings passed to
      ``send_message`` (newest last).
    * ``edited_texts`` — the ordered list of strings passed to
      ``edit_message_text``.
    * ``chat_actions`` — the actions recorded via ``send_chat_action``.
    """
    sent_texts: list[str] = []
    edited_texts: list[str] = []
    chat_actions: list[str] = []

    async def _send_message(
        chat_id: int | str, text: str, **_kwargs: Any
    ) -> SimpleNamespace:
        sent_texts.append(text)
        return SimpleNamespace(
            message_id=100 + len(sent_texts),
            chat_id=chat_id,
            text=text,
        )

    async def _edit_message_text(
        text: str,
        *,
        chat_id: int | str | None = None,
        message_id: int | None = None,
        **_kwargs: Any,
    ) -> SimpleNamespace:
        edited_texts.append(text)
        return SimpleNamespace(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
        )

    async def _send_chat_action(
        chat_id: int | str, action: str, **_kwargs: Any
    ) -> bool:
        chat_actions.append(action)
        return True

    bot = MagicMock(name="MockTelegramBot")
    bot.send_message = AsyncMock(side_effect=_send_message)
    bot.edit_message_text = AsyncMock(side_effect=_edit_message_text)
    bot.send_chat_action = AsyncMock(side_effect=_send_chat_action)

    return SimpleNamespace(
        bot=bot,
        sent_texts=sent_texts,
        edited_texts=edited_texts,
        chat_actions=chat_actions,
    )


# --------------------------------------------------------------------------- #
# console_client — stdin/stdout plumbing for the console adapter
# --------------------------------------------------------------------------- #


class _ConsoleClient:
    """Paired StringIO buffers so tests can feed stdin and scrape stdout.

    The adapter reads from ``sys.stdin`` via ``run_in_executor(None, readline)``;
    we hand it a ``io.StringIO`` whose content is populated line-by-line.
    """

    def __init__(self) -> None:
        self.stdin = io.StringIO()
        self.stdout = io.StringIO()
        self._stdin_lines: list[str] = []

    def feed(self, line: str) -> None:
        """Queue ``line`` (newline auto-appended if missing) to stdin."""
        if not line.endswith("\n"):
            line = line + "\n"
        self._stdin_lines.append(line)
        # Rebuild the StringIO so the next ``readline`` sees pending lines.
        remaining = "".join(self._stdin_lines)
        self.stdin = io.StringIO(remaining)
        self._stdin_lines = [remaining]

    def output(self) -> str:
        return self.stdout.getvalue()


@pytest.fixture
def console_client(monkeypatch: pytest.MonkeyPatch) -> _ConsoleClient:
    """Provide an in-memory stdin/stdout pair for the console adapter."""
    client = _ConsoleClient()
    monkeypatch.setattr(sys, "stdin", client.stdin)
    return client


__all__ = [
    "console_client",
    "fleet_duo",
    "graph_store_inmem",
    "mock_telegram",
    "settings_store_inmem",
    "tmp_vyasa_home",
]
