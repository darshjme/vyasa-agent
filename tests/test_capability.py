"""Tests for the capability matrix + 3-layer enforcement hooks."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from vyasa_agent.fleet.audit import AuditRecord, AuditSink
from vyasa_agent.fleet.capability import (
    Capability,
    CapabilityError,
    CapabilityMatrix,
    Decision,
)
from vyasa_agent.fleet.hooks import (
    boot_tool_filter,
    post_tool_call,
    pre_tool_call,
)
from vyasa_agent.fleet.tool_name_to_capability import TOOL_TO_CAPABILITY

REPO_ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = REPO_ROOT / "capabilities.yaml"


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@dataclass
class FakeDescriptor:
    id: str
    allowed_tools: list[str] = field(default_factory=list)


class RecordingApprovalSink:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def request_approval(
        self,
        *,
        employee_id: str,
        capability: Capability,
        tool_name: str,
        args_hash: str,
        trace_id: str,
        rationale: str,
    ) -> None:
        self.requests.append(
            {
                "employee_id": employee_id,
                "capability": capability,
                "tool_name": tool_name,
                "args_hash": args_hash,
                "trace_id": trace_id,
                "rationale": rationale,
            }
        )


@pytest.fixture(scope="module")
def matrix() -> CapabilityMatrix:
    return CapabilityMatrix.load(MATRIX_PATH)


@pytest.fixture()
def audit_sink(tmp_path: Path) -> AuditSink:
    return AuditSink(root=tmp_path / "audit")


# ---------------------------------------------------------------------------
# Matrix loading + shape
# ---------------------------------------------------------------------------


def test_matrix_loads_all_28_employees(matrix: CapabilityMatrix) -> None:
    employees = matrix.employees()
    assert len(employees) == 28, employees
    # spot-check one from each registry
    assert "vyasa" in employees
    assert "prometheus" in employees
    assert "dr-sarabhai" in employees
    assert "dr-reddy" in employees


def test_every_employee_has_all_20_capabilities(matrix: CapabilityMatrix) -> None:
    expected = {c for c in Capability if c is not Capability.UNKNOWN}
    for employee_id, row in matrix.cells.items():
        assert set(row) == expected, (
            f"{employee_id} is missing cells: {expected - set(row)}"
        )


def test_every_rationale_under_80_chars(matrix: CapabilityMatrix) -> None:
    for employee_id, row in matrix.cells.items():
        for cap, cell in row.items():
            assert len(cell.rationale) <= 80, (
                f"{employee_id}/{cap.value} rationale too long: {len(cell.rationale)}"
            )


# ---------------------------------------------------------------------------
# check() + explain()
# ---------------------------------------------------------------------------


def test_allow_cell(matrix: CapabilityMatrix) -> None:
    assert matrix.check("prometheus", Capability.BASH) is Decision.ALLOW


def test_deny_cell(matrix: CapabilityMatrix) -> None:
    assert matrix.check("vyasa", Capability.BASH) is Decision.DENY


def test_require_approval_cell(matrix: CapabilityMatrix) -> None:
    assert matrix.check("kavach", Capability.BASH) is Decision.REQUIRE_APPROVAL


def test_unknown_employee_denied(matrix: CapabilityMatrix) -> None:
    assert matrix.check("ghost", Capability.FS_READ) is Decision.DENY


def test_unknown_capability_denied(matrix: CapabilityMatrix) -> None:
    assert matrix.check("vyasa", Capability.UNKNOWN) is Decision.DENY


def test_explain_returns_rationale(matrix: CapabilityMatrix) -> None:
    text = matrix.explain("prometheus", Capability.BASH)
    assert "build" in text or "test" in text, text


# ---------------------------------------------------------------------------
# Layer A — boot filter
# ---------------------------------------------------------------------------


def test_boot_filter_drops_denied_tools(matrix: CapabilityMatrix) -> None:
    descriptor = FakeDescriptor(
        id="vyasa",
        allowed_tools=["file_read", "run_bash", "web_fetch", "delegate"],
    )
    allowed = boot_tool_filter(
        descriptor, ["file_read", "run_bash", "web_fetch", "delegate"], matrix=matrix
    )
    assert "run_bash" not in allowed  # vyasa bash=deny
    assert "file_read" in allowed
    assert "web_fetch" in allowed
    assert "delegate" in allowed


def test_boot_filter_keeps_require_approval(matrix: CapabilityMatrix) -> None:
    descriptor = FakeDescriptor(
        id="kavach",
        allowed_tools=["run_bash", "file_read"],
    )
    allowed = boot_tool_filter(
        descriptor, ["run_bash", "file_read"], matrix=matrix
    )
    # require_approval keeps the tool visible so the runtime hook can gate it
    assert "run_bash" in allowed
    assert "file_read" in allowed


def test_boot_filter_drops_unmapped_tools(matrix: CapabilityMatrix) -> None:
    descriptor = FakeDescriptor(
        id="prometheus", allowed_tools=["file_read", "mystery_tool"]
    )
    allowed = boot_tool_filter(
        descriptor, ["file_read", "mystery_tool"], matrix=matrix
    )
    assert "mystery_tool" not in allowed


# ---------------------------------------------------------------------------
# Layer B — runtime pre-check
# ---------------------------------------------------------------------------


async def test_pre_tool_call_allow_passes(
    matrix: CapabilityMatrix, audit_sink: AuditSink
) -> None:
    # Should not raise.
    await pre_tool_call(
        "prometheus",
        "run_bash",
        {"cmd": "pytest"},
        matrix,
        trace_id="t1",
        audit_sink=audit_sink,
    )


async def test_pre_tool_call_deny_raises(
    matrix: CapabilityMatrix, audit_sink: AuditSink
) -> None:
    with pytest.raises(CapabilityError) as excinfo:
        await pre_tool_call(
            "vyasa",
            "run_bash",
            {"cmd": "ls"},
            matrix,
            trace_id="t2",
            audit_sink=audit_sink,
        )
    err = excinfo.value
    assert err.decision is Decision.DENY
    assert err.employee_id == "vyasa"
    assert err.capability is Capability.BASH
    assert err.rationale


async def test_pre_tool_call_require_approval_logs_request(
    matrix: CapabilityMatrix, audit_sink: AuditSink
) -> None:
    approval = RecordingApprovalSink()
    with pytest.raises(CapabilityError):
        await pre_tool_call(
            "kavach",
            "run_bash",
            {"cmd": "nmap"},
            matrix,
            trace_id="t3",
            approval_sink=approval,
            audit_sink=audit_sink,
        )
    assert len(approval.requests) == 1
    req = approval.requests[0]
    assert req["employee_id"] == "kavach"
    assert req["capability"] is Capability.BASH
    assert req["tool_name"] == "run_bash"
    assert req["trace_id"] == "t3"


async def test_pre_tool_call_unknown_tool_denied(
    matrix: CapabilityMatrix, audit_sink: AuditSink
) -> None:
    with pytest.raises(CapabilityError) as excinfo:
        await pre_tool_call(
            "prometheus",
            "totally_unmapped_tool",
            {},
            matrix,
            audit_sink=audit_sink,
        )
    assert excinfo.value.capability is Capability.UNKNOWN


# ---------------------------------------------------------------------------
# Layer C — audit sink
# ---------------------------------------------------------------------------


async def test_audit_sink_writes_jsonl(audit_sink: AuditSink) -> None:
    record = AuditRecord(
        employee_id="prometheus",
        tool_name="run_bash",
        decision=Decision.ALLOW,
        args_hash=AuditRecord.hash_args({"cmd": "pytest"}),
        duration_ms=42,
        trace_id="trace-a",
        rationale="allow bash",
        result_summary="ok",
    )
    await audit_sink.append(record)

    jsonl_files = list(audit_sink.root.glob("audit-*.jsonl"))
    assert len(jsonl_files) == 1
    line = jsonl_files[0].read_text(encoding="utf-8").strip()
    payload = json.loads(line)
    assert payload["employee_id"] == "prometheus"
    assert payload["decision"] == "allow"
    assert payload["tool_name"] == "run_bash"
    assert payload["trace_id"] == "trace-a"


async def test_post_tool_call_appends_record(audit_sink: AuditSink) -> None:
    await post_tool_call(
        "prometheus",
        "run_bash",
        "pytest passed",
        150,
        audit_sink,
        trace_id="trace-b",
        args={"cmd": "pytest"},
    )
    jsonl_files = list(audit_sink.root.glob("audit-*.jsonl"))
    assert jsonl_files
    line = jsonl_files[0].read_text(encoding="utf-8").splitlines()[-1]
    payload = json.loads(line)
    assert payload["employee_id"] == "prometheus"
    assert payload["decision"] == "allow"
    assert payload["duration_ms"] == 150


async def test_audit_sink_double_writes_sqlite(audit_sink: AuditSink) -> None:
    import sqlite3

    await post_tool_call(
        "prometheus",
        "run_bash",
        "ok",
        10,
        audit_sink,
        trace_id="trace-sql",
        args={"cmd": "true"},
    )
    db = audit_sink.root / "tool_audit.sqlite"
    assert db.exists()
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT employee_id, tool_name, decision FROM tool_audit"
        ).fetchall()
    assert ("prometheus", "run_bash", "allow") in rows


# ---------------------------------------------------------------------------
# Tool-to-capability mapping sanity
# ---------------------------------------------------------------------------


def test_common_tool_mappings() -> None:
    assert TOOL_TO_CAPABILITY["run_bash"] is Capability.BASH
    assert TOOL_TO_CAPABILITY["web_fetch"] is Capability.WEB_FETCH
    assert TOOL_TO_CAPABILITY["image_gen"] is Capability.IMAGE_GEN
    assert TOOL_TO_CAPABILITY["tg_send"] is Capability.GATEWAY_TELEGRAM


def test_mapping_covers_at_least_25_tools() -> None:
    assert len(TOOL_TO_CAPABILITY) >= 25
