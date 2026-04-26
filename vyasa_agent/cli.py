# SPDX-License-Identifier: Apache-2.0
"""``vyasa`` CLI entry point (Fire). Groups: gateway / employee / graph,
plus ``doctor`` and ``version``. ``gateway serve`` boots fleet + adapters +
admin panel on port 19000, drains within 30s on SIGTERM. Exits: 0 ok, 1
user error, 2 runtime failure. ``doctor`` / ``version`` do zero I/O.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any

from rich.console import Console

from . import cli_support

VERSION = "0.1.0a1"
DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 19000
_EXPECTED_EMPLOYEE_COUNT = 29
_LEGAL_ROSTERS = ("vyasa", "graymatter")


def _stdout() -> Console:
    return Console()

def _default_fleet_root() -> Path:
    root = os.environ.get("VYASA_FLEET_ROOT")
    return Path(root).expanduser().resolve() if root else cli_support.repo_root()


def _settings_store(path: Path | None = None) -> Any:
    from .admin_panel.settings_store import SettingsStore
    target = path or Path.home() / ".vyasa" / "settings.sqlite"
    target.parent.mkdir(parents=True, exist_ok=True)
    return SettingsStore(target)


def _load_descriptors(fleet_root: Path) -> list[Any]:
    from .fleet.descriptor import load_fleet
    return load_fleet(fleet_root)[1]


def _pick_descriptors(roster: str | None, fleet_root: Path) -> list[Any]:
    if roster is not None and roster not in _LEGAL_ROSTERS:
        raise UserError(f"unknown roster {roster!r}; choose one of {_LEGAL_ROSTERS}")
    descriptors = _load_descriptors(fleet_root)
    return descriptors if roster is None else [d for d in descriptors if d.registry_source == roster]


class UserError(Exception):
    """User-facing input error; ``main`` maps it to exit code 1."""


class RuntimeFailure(Exception):
    """Unexpected runtime failure; ``main`` maps it to exit code 2."""


class _EmployeeCommands:
    """``vyasa employee ...``"""

    def list(self, roster: str | None = None) -> None:
        """Print directory rows (id, display_name, role, model, enabled)."""
        descriptors = _pick_descriptors(roster, _default_fleet_root())
        store = _settings_store()
        rows: list[dict[str, Any]] = []
        for d in descriptors:
            enabled = store.get(f"fleet.employee.{d.id}.enabled")
            rows.append({
                "id": d.id, "display_name": d.display_name, "role_key": d.role_key,
                "model": d.model_preference.default,
                "enabled": True if enabled is None else bool(enabled),
            })
        title = f"vyasa fleet ({roster or 'all'}) — {len(rows)} employees"
        _stdout().print(cli_support.employee_table(rows, title=title))

    def show(self, employee_id: str) -> None:
        """Print the descriptor + a 300-char preview of the resolved prompt."""
        from .fleet.registry_resolver import PromptResolutionError, resolve_prompt
        fleet_root = _default_fleet_root()
        match = next((d for d in _load_descriptors(fleet_root) if d.id == employee_id), None)
        if match is None:
            raise UserError(f"unknown employee id: {employee_id!r}")
        console = _stdout()
        console.print(f"[bold cyan]{match.id}[/bold cyan] — {match.display_name}")
        console.print(match.model_dump())
        try:
            prompt = resolve_prompt(match.system_prompt_ref, fleet_root=fleet_root)
        except PromptResolutionError as exc:
            console.print(f"[red]prompt resolution failed:[/red] {exc}")
            return
        preview = prompt[:300] + ("..." if len(prompt) > 300 else "")
        console.print(f"[bold]prompt[/bold] ({prompt.count(chr(10)) + 1} lines):\n{preview}")

    def enable(self, employee_id: str) -> None:
        """Set ``fleet.employee.<id>.enabled`` to ``True``."""
        self._set_enabled(employee_id, True)

    def disable(self, employee_id: str) -> None:
        """Set ``fleet.employee.<id>.enabled`` to ``False``."""
        self._set_enabled(employee_id, False)

    def _set_enabled(self, employee_id: str, value: bool) -> None:
        if employee_id not in {d.id for d in _load_descriptors(_default_fleet_root())}:
            raise UserError(f"unknown employee id: {employee_id!r}")
        _settings_store().set(
            f"fleet.employee.{employee_id}.enabled", value, user="cli", section="fleet",
        )
        _stdout().print(f"[green]{employee_id}[/green] -> enabled={value}")


class _GraphCommands:
    """``vyasa graph ...``"""

    def query(self, intent: str, visibility: str | None = None, limit: int = 10) -> None:
        """Pretty-print graph nodes matching ``intent``."""
        from .graphify.store import GraphStore
        from .graphify.types import QueryFilters
        store = GraphStore()
        filters = QueryFilters(
            intent=intent, limit=limit,
            visibility_scope=[visibility] if visibility else None,  # type: ignore[arg-type]
        )
        try:
            nodes = asyncio.run(store.query(filters))
        finally:
            asyncio.run(store.close())
        _stdout().print(cli_support.render_graph_nodes(nodes))

    def migrate(self) -> None:
        """Run the v1->v2 migrator into ``~/.vyasa/graph.sqlite``."""
        import runpy
        source = Path.home() / "graymatter-online-llp" / "graymatter_kb" / "context_graph.json"
        target = Path.home() / ".vyasa" / "graph.sqlite"
        target.parent.mkdir(parents=True, exist_ok=True)
        script = cli_support.repo_root() / "scripts" / "migrate_graph_v1_to_v2.py"
        if not source.exists():
            raise UserError(f"source graph not found: {source}")
        if not script.exists():
            raise RuntimeFailure(f"migrator missing: {script}")
        saved, sys.argv = sys.argv, [str(script), "--source", str(source), "--target", str(target)]
        try:
            runpy.run_path(str(script), run_name="__main__")
        finally:
            sys.argv = saved


class _GatewayCommands:
    """``vyasa gateway ...``"""

    def serve(self, bind: str = DEFAULT_BIND, port: int = DEFAULT_PORT,
              telegram: bool = False, console: bool = False) -> None:
        """Boot fleet + adapters + admin panel; block until SIGTERM."""
        use_console = console or (cli_support.is_tty() and not telegram)
        cli_support.configure_logging(headless=not use_console)
        try:
            asyncio.run(_serve(bind=bind, port=port, telegram=telegram, console=use_console))
        except KeyboardInterrupt:
            logging.getLogger("vyasa.cli").info("gateway.interrupted")


async def _serve(*, bind: str, port: int, telegram: bool, console: bool) -> None:
    from .admin_panel.app import create_app
    from .fleet.manager import FleetManager
    from .gateway.adapters.console import ConsoleAdapter
    from .graphify.store import GraphStore
    log = logging.getLogger("vyasa.cli")
    shutdown = cli_support.GracefulShutdown()
    shutdown.install()
    fleet = FleetManager()
    await fleet.boot(_default_fleet_root())
    log.info("gateway.fleet_booted", extra={"employees": len(fleet.employee_ids)})
    graph_store = GraphStore()
    app = create_app(fleet, graph_store, _settings_store())
    _, stop_uvicorn = cli_support.run_uvicorn_in_thread(app, host=bind, port=port)
    log.info("gateway.admin_ready", extra={"bind": bind, "port": port})

    adapters: list[Any] = []
    if console:
        c = ConsoleAdapter(); c.bind_inbound(_inbound_handler()); await c.start(); adapters.append(c)
    if telegram:
        from .gateway.adapters.telegram import TelegramAdapter
        tg = TelegramAdapter(); tg.bind_inbound(_inbound_handler()); await tg.start(); adapters.append(tg)
    if not console:
        log.info("gateway.ready_headless")

    try:
        await shutdown.wait()
    finally:
        log.info("gateway.shutdown.begin")
        for adapter in adapters:
            try: await adapter.stop()
            except Exception as exc:  # pragma: no cover
                log.warning("gateway.adapter.stop_failed", extra={"err": str(exc)})
        try: await asyncio.wait_for(fleet.shutdown(), timeout=cli_support.DEFAULT_SHUTDOWN_TIMEOUT_S)
        except TimeoutError: log.warning("gateway.shutdown.timeout")
        await graph_store.close()
        stop_uvicorn()
        log.info("gateway.shutdown.complete")


def _inbound_handler():
    async def _handler(msg: Any) -> None:
        logging.getLogger("vyasa.cli").info("gateway.inbound",
            extra={"trace_id": getattr(msg, "trace_id", None)})
    return _handler


class VyasaCLI:
    """Top-level Fire component."""

    def __init__(self) -> None:
        self.gateway = _GatewayCommands()
        self.employee = _EmployeeCommands()
        self.graph = _GraphCommands()

    def version(self) -> None:
        """Print the CLI version."""
        _stdout().print(VERSION)

    def doctor(self) -> None:
        """Run offline self-checks; raise :class:`RuntimeFailure` on any failure."""
        checks = _run_doctor_checks()
        _stdout().print(cli_support.render_doctor_report(checks))
        if not all(ok for _, ok, _ in checks):
            raise RuntimeFailure("doctor reported one or more failures")


def _run_doctor_checks() -> list[tuple[str, bool, str]]:
    out: list[tuple[str, bool, str]] = []
    try:
        import vyasa_agent  # noqa: F401
        out.append(("runtime importable", True, "vyasa_agent loaded"))
    except Exception as exc:  # pragma: no cover
        out.append(("runtime importable", False, str(exc)))
    try:
        import yaml
        data = yaml.safe_load((cli_support.repo_root() / "capabilities.yaml").read_text("utf-8")) or {}
        out.append(("capabilities.yaml", True, f"{len(data)} employees defined"))
    except Exception as exc:
        out.append(("capabilities.yaml", False, str(exc)))
    yamls = sorted((cli_support.repo_root() / "employees").glob("*.yaml"))
    ok = len(yamls) == _EXPECTED_EMPLOYEE_COUNT
    out.append(("employees/*.yaml", ok, f"{len(yamls)}/{_EXPECTED_EMPLOYEE_COUNT} descriptors"))
    try:
        from .admin_panel.settings_store import SettingsStore
        probe = SettingsStore(":memory:")
        probe.set("doctor.probe", True, user="cli-doctor", section="diagnostics")
        probe.close()
        out.append(("settings store", True, "sqlite in-memory round-trip ok"))
    except Exception as exc:
        out.append(("settings store", False, str(exc)))
    return out


def main(argv: list[str] | None = None) -> int:
    """Entry point registered in :file:`pyproject.toml`."""
    import fire
    try:
        fire.Fire(VyasaCLI, command=argv, name="vyasa")
    except UserError as exc:
        _stdout().print(f"[red]error:[/red] {exc}"); return 1
    except RuntimeFailure as exc:
        _stdout().print(f"[red]runtime failure:[/red] {exc}"); return 2
    except SystemExit as exc:
        # Fire uses SystemExit with a string payload to carry help text or
        # error messages. A string payload means a user-facing error;
        # return 1 so the shell can tell success from failure (previously
        # we returned 0 for any non-int, masking Fire's own argparse errors).
        if isinstance(exc.code, int):
            return exc.code
        return 0 if exc.code is None else 1
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # pragma: no cover
        logging.getLogger("vyasa.cli").exception("cli.unhandled")
        _stdout().print(f"[red]unhandled error:[/red] {exc}")
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
