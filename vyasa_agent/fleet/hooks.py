"""Three-layer capability enforcement hooks.

- **Layer A — Boot-time filter.** :func:`boot_tool_filter` trims a tool
  registry down to the subset an employee is allowed to see. Disallowed
  tools never enter the model's function schema, so they cannot be
  emitted.
- **Layer B — Runtime pre-tool check.** :func:`pre_tool_call` runs before
  every tool invocation. ``ALLOW`` continues, ``DENY`` raises
  :class:`CapabilityError`, and ``REQUIRE_APPROVAL`` emits an approval
  request node through the supplied sink (if any) and then raises.
- **Layer C — Post-tool audit.** :func:`post_tool_call` records the
  outcome into the :class:`AuditSink`.

All three accept typed inputs; hook wiring into the hermes plugin
manager happens in the runtime layer, not here.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable
from typing import Any, Protocol

from .audit import AuditRecord, AuditSink
from .capability import (
    Capability,
    CapabilityError,
    CapabilityMatrix,
    Decision,
)
from .tool_name_to_capability import lookup as lookup_capability

logger = logging.getLogger(__name__)


class _DescriptorLike(Protocol):
    id: str
    allowed_tools: list[str]


class _ApprovalSink(Protocol):
    """Minimal interface the approval graph node writer must satisfy."""

    async def request_approval(
        self,
        *,
        employee_id: str,
        capability: Capability,
        tool_name: str,
        args_hash: str,
        trace_id: str,
        rationale: str,
    ) -> None: ...


# ---------------------------------------------------------------------------
# Layer A — boot-time registry filter
# ---------------------------------------------------------------------------


def boot_tool_filter(
    descriptor: _DescriptorLike,
    full_registry: Iterable[str],
    *,
    matrix: CapabilityMatrix | None = None,
) -> list[str]:
    """Return the subset of ``full_registry`` allowed for ``descriptor``.

    Two filters compose:

    1. The employee's ``allowed_tools`` declared in its YAML descriptor.
    2. The matrix: a tool passes only if its mapped capability resolves to
       :attr:`Decision.ALLOW` for this employee. ``REQUIRE_APPROVAL`` tools
       are still exposed (the runtime hook will intercept), but ``DENY``
       and unmapped tools are dropped.

    If ``matrix`` is ``None`` the filter falls back to the descriptor's
    declared allowlist alone.
    """
    allowed_declared = set(descriptor.allowed_tools)
    allowed: list[str] = []
    for tool_name in full_registry:
        if allowed_declared and tool_name not in allowed_declared:
            continue
        if matrix is not None:
            cap = lookup_capability(tool_name)
            if cap is Capability.UNKNOWN:
                logger.debug(
                    "boot filter drop %s for %s: no capability mapping",
                    tool_name,
                    descriptor.id,
                )
                continue
            decision = matrix.check(descriptor.id, cap)
            if decision is Decision.DENY:
                continue
        allowed.append(tool_name)
    return allowed


# ---------------------------------------------------------------------------
# Layer B — runtime pre-tool check
# ---------------------------------------------------------------------------


async def pre_tool_call(
    employee_id: str,
    tool_name: str,
    args: dict[str, Any],
    matrix: CapabilityMatrix,
    *,
    trace_id: str = "",
    approval_sink: _ApprovalSink | None = None,
    audit_sink: AuditSink | None = None,
) -> None:
    """Runtime gate for a single tool invocation.

    Raises:
        CapabilityError: tool is ``DENY`` or ``REQUIRE_APPROVAL`` without
            a pre-granted approval. The caller converts this into a
            tool-error message so the agent loop survives.
    """
    capability = lookup_capability(tool_name)
    decision = matrix.check(employee_id, capability)
    rationale = matrix.explain(employee_id, capability)

    if decision is Decision.ALLOW:
        return

    args_hash = AuditRecord.hash_args(args)

    if decision is Decision.REQUIRE_APPROVAL and approval_sink is not None:
        await approval_sink.request_approval(
            employee_id=employee_id,
            capability=capability,
            tool_name=tool_name,
            args_hash=args_hash,
            trace_id=trace_id,
            rationale=rationale,
        )

    if audit_sink is not None:
        await audit_sink.append(
            AuditRecord(
                employee_id=employee_id,
                tool_name=tool_name,
                decision=decision,
                args_hash=args_hash,
                duration_ms=0,
                trace_id=trace_id,
                rationale=rationale,
            )
        )

    raise CapabilityError(
        decision=decision,
        employee_id=employee_id,
        capability=capability,
        rationale=rationale,
    )


# ---------------------------------------------------------------------------
# Layer C — post-tool audit
# ---------------------------------------------------------------------------


async def post_tool_call(
    employee_id: str,
    tool_name: str,
    result_summary: str,
    duration_ms: int,
    audit_sink: AuditSink,
    *,
    trace_id: str = "",
    args: dict[str, Any] | None = None,
) -> None:
    """Append the ALLOW-path audit record after a tool invocation finishes."""
    capability = lookup_capability(tool_name)
    record = AuditRecord(
        employee_id=employee_id,
        tool_name=tool_name,
        decision=Decision.ALLOW,
        args_hash=AuditRecord.hash_args(args or {}),
        duration_ms=max(0, int(duration_ms)),
        trace_id=trace_id,
        rationale=f"allow {capability.value}",
        result_summary=(result_summary or "")[:512],
    )
    await audit_sink.append(record)


ApprovalRequester = Callable[..., Awaitable[None]]

__all__ = [
    "ApprovalRequester",
    "boot_tool_filter",
    "post_tool_call",
    "pre_tool_call",
]
