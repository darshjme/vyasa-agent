"""Tests for :mod:`vyasa_agent.cli`.

Exercises the Fire dispatcher via :func:`cli.main` using the ``argv``
argument. Network I/O is forbidden in ``doctor`` / ``version`` paths, so
these tests run fully offline against the repo checkout.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

from vyasa_agent import cli, cli_support

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _isolate_settings(tmp_path, monkeypatch):
    """Redirect the per-user SettingsStore to a temp path for every test."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(Path, "home", lambda: fake_home, raising=True)
    monkeypatch.setenv("VYASA_FLEET_ROOT", str(REPO_ROOT))
    yield


@pytest.fixture
def capture_stdout(monkeypatch):
    """Force :func:`cli._stdout` to return a capturing :class:`Console`."""
    from rich.console import Console

    buf = io.StringIO()
    console = Console(file=buf, width=200, force_terminal=False, record=True)
    monkeypatch.setattr(cli, "_stdout", lambda: console)
    return buf


def test_version_prints_0_1_0a1(capture_stdout):
    rc = cli.main(["version"])
    assert rc == 0
    out = capture_stdout.getvalue().strip()
    assert out == cli.VERSION == "0.2.0"


def test_doctor_exits_zero_on_fresh_repo(capture_stdout):
    rc = cli.main(["doctor"])
    assert rc == 0, f"doctor failed. output=\n{capture_stdout.getvalue()}"
    text = capture_stdout.getvalue()
    assert "vyasa doctor" in text
    assert "FAIL" not in text, text


def test_doctor_non_network():
    """Sanity check: doctor builds a check list without touching the network."""
    checks = cli._run_doctor_checks()
    labels = [c[0] for c in checks]
    assert "runtime importable" in labels
    assert "capabilities.yaml" in labels
    assert "employees/*.yaml" in labels
    assert "settings store" in labels
    # 29 descriptors are required on a clean repo.
    employees_check = dict((c[0], c) for c in checks)["employees/*.yaml"]
    assert employees_check[1] is True, employees_check


def test_employee_list_prints_29_rows(capture_stdout):
    from vyasa_agent.fleet.descriptor import load_fleet

    rc = cli.main(["employee", "list"])
    assert rc == 0
    body = capture_stdout.getvalue()
    _, descriptors = load_fleet(REPO_ROOT)
    assert len(descriptors) == 29
    # The rendered title line always contains the count.
    assert "29 employees" in body
    # Every employee id (as declared in YAML) should appear in the table body.
    missing = [d.id for d in descriptors if d.id not in body]
    assert not missing, f"missing ids in output: {missing}\n{body}"


def test_employee_list_roster_filter(capture_stdout):
    rc = cli.main(["employee", "list", "--roster", "vyasa"])
    assert rc == 0
    body = capture_stdout.getvalue()
    # Graymatter doctors should NOT appear in the vyasa-only roster.
    assert "dr.sarabhai" not in body
    assert "dr-sarabhai" not in body
    assert "vyasa" in body


def test_employee_list_rejects_unknown_roster(capture_stdout):
    rc = cli.main(["employee", "list", "--roster", "martian"])
    assert rc == 1
    assert "unknown roster" in capture_stdout.getvalue()


def test_employee_enable_then_disable_round_trip(capture_stdout):
    rc_enable = cli.main(["employee", "enable", "vyasa"])
    rc_disable = cli.main(["employee", "disable", "vyasa"])
    assert rc_enable == 0 and rc_disable == 0
    body = capture_stdout.getvalue()
    assert "vyasa -> enabled=True" in body
    assert "vyasa -> enabled=False" in body


def test_employee_enable_unknown_id(capture_stdout):
    rc = cli.main(["employee", "enable", "does-not-exist"])
    assert rc == 1
    assert "unknown employee id" in capture_stdout.getvalue()


def test_gateway_serve_wires_fleet_manager(monkeypatch, tmp_path):
    """Real fleet and stores share the server loop and close cleanly."""
    import uvicorn
    observed = {}
    monkeypatch.setenv("VYASA_HOME", str(tmp_path))
    class Server:
        def __init__(self, config):
            self.config = config
        async def serve(self):
            app = self.config.app
            service = app.state.fleet_manager
            observed["service"] = service
            assert len(service.directory()) == 29
            assert service.is_alive("prometheus")
            observed["port"] = self.config.port
    monkeypatch.setattr(uvicorn, "Server", Server)
    monkeypatch.setattr(cli_support, "is_tty", lambda: False)
    assert cli.main(["gateway", "serve", "--port", "0"]) == 0
    assert observed["port"] == 0
    assert not observed["service"].is_alive("prometheus")
