"""Golden ``vyasa doctor`` self-check tests.

The doctor path is pure offline: no network, no SQLite file on disk
for the healthy case (settings-store probe uses ``:memory:``).  A
``capabilities.yaml`` shape bug exits ``2``; the healthy repo exits
``0``; every check line names the probe.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]


# --------------------------------------------------------------------------- #
# 1. Healthy repo → exit 0
# --------------------------------------------------------------------------- #


def test_doctor_healthy_repo_exits_zero(
    tmp_vyasa_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from vyasa_agent import cli as cli_module

    # Force the doctor to probe *this* checkout so the employees/ count
    # and capabilities.yaml checks evaluate against known-good data.
    repo_root = Path(__file__).resolve().parents[2]
    monkeypatch.setattr(cli_module.cli_support, "repo_root", lambda: repo_root)

    rc = cli_module.main(["doctor"])
    out = capsys.readouterr().out

    assert rc == 0, f"doctor exit code {rc}; output=\n{out}"
    assert "runtime importable" in out
    assert "capabilities.yaml" in out
    assert "employees" in out.lower()
    assert "settings store" in out.lower()


# --------------------------------------------------------------------------- #
# 2. Broken capabilities.yaml → exit 2
# --------------------------------------------------------------------------- #


def test_doctor_broken_capabilities_exits_two(
    tmp_vyasa_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A shape-bug in capabilities.yaml must surface as a non-zero doctor exit.

    We construct a fake repo root whose ``capabilities.yaml`` is invalid
    YAML and point the doctor's ``repo_root`` helper at it.  Every other
    file is left missing — the doctor treats that as additional probe
    failures (employees count, etc.) which is exactly the condition we
    want exit 2 to capture.
    """
    fake_root = tmp_path / "fake-repo"
    fake_root.mkdir()
    # Invalid YAML — the colon-less line triggers a scan error.
    (fake_root / "capabilities.yaml").write_text(
        "this: is: not: a: mapping: of: employees:\n  - broken\n",
        encoding="utf-8",
    )

    from vyasa_agent import cli as cli_module

    monkeypatch.setattr(cli_module.cli_support, "repo_root", lambda: fake_root)

    rc = cli_module.main(["doctor"])
    out = capsys.readouterr().out

    assert rc == 2, f"expected exit 2 on broken capabilities, got {rc}\n{out}"
    # Even with the failure, the report must name each check the doctor ran.
    assert "capabilities.yaml" in out
    assert "employees" in out.lower()


# --------------------------------------------------------------------------- #
# 3. Every probe emits a diagnosis line
# --------------------------------------------------------------------------- #


def test_doctor_prints_a_line_per_check(
    tmp_vyasa_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from vyasa_agent import cli as cli_module

    repo_root = Path(__file__).resolve().parents[2]
    monkeypatch.setattr(cli_module.cli_support, "repo_root", lambda: repo_root)

    cli_module.main(["doctor"])
    out = capsys.readouterr().out

    # The rich table renders each row; check label visibility.
    for needle in ("runtime importable", "capabilities.yaml", "settings store"):
        assert needle in out, f"doctor output missing probe {needle!r}:\n{out}"


__all__: list[str] = []
