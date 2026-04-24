"""Capability matrix — per-employee tool-scope governance.

Implements the per-employee capability enum, the decision enum, the
``CapabilityMatrix`` loader, and the ``CapabilityError`` raised by Layer B
runtime enforcement.

The matrix itself lives in a sibling YAML file (``capabilities.yaml`` at the
repo root by default). Every cell is a typed ``CapabilityCell`` with an
explicit ``decision`` and a one-line ``rationale`` used for audit output.

No hermes, no registry imports — this module stays dependency-light so the
boot filter, the pre-tool hook, and the admin panel can all import it without
pulling the runtime graph.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class Capability(str, Enum):  # noqa: UP042 — explicit (str, Enum) per spec
    """The 20 typed capabilities governed by the matrix."""

    FS_READ = "fs_read"
    FS_WRITE = "fs_write"
    BASH = "bash"
    GIT = "git"
    DOCKER = "docker"
    WEB_FETCH = "web_fetch"
    WEB_SEARCH = "web_search"
    GATEWAY_TELEGRAM = "gateway_telegram"
    GATEWAY_WHATSAPP = "gateway_whatsapp"
    GATEWAY_SLACK = "gateway_slack"
    DELEGATE = "delegate"
    SPAWN_SUBAGENT = "spawn_subagent"
    CODE_EXEC = "code_exec"
    VISION = "vision"
    STT = "stt"
    TTS = "tts"
    PENTEST = "pentest"
    ENVATO_VERIFY = "envato_verify"
    IMAGE_GEN = "image_gen"
    DATA_PIPELINES = "data_pipelines"

    # Sentinel for tools that have no mapping into the enum. Always DENY.
    UNKNOWN = "unknown"


class Decision(str, Enum):  # noqa: UP042 — explicit (str, Enum) per spec
    """The tri-state decision output from the matrix."""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class CapabilityCell(BaseModel):
    """One (employee, capability) cell: decision + audit rationale."""

    model_config = ConfigDict(extra="forbid")

    decision: Decision
    rationale: str = Field(..., min_length=1, max_length=200)

    @field_validator("rationale")
    @classmethod
    def _trim(cls, v: str) -> str:
        return v.strip()


class CapabilityError(Exception):
    """Raised by Layer B when a tool invocation is not allowed.

    Carries the decision, employee id, capability, and audit rationale so
    the runtime can both log the block and surface a structured tool-error
    message back into the agent loop without leaking internals to the user.
    """

    def __init__(
        self,
        *,
        decision: Decision,
        employee_id: str,
        capability: Capability,
        rationale: str,
    ) -> None:
        self.decision = decision
        self.employee_id = employee_id
        self.capability = capability
        self.rationale = rationale
        super().__init__(
            f"capability denied: employee={employee_id} capability={capability.value} "
            f"decision={decision.value} rationale={rationale!r}"
        )


class CapabilityMatrix(BaseModel):
    """Parsed 28x20 capability matrix.

    Populated from ``capabilities.yaml``. The outer key is the employee id;
    the inner key is the capability enum value. Every call to :meth:`check`
    returns a :class:`Decision` sourced from the matrix. Unknown employee
    or unknown capability both default-deny.
    """

    model_config = ConfigDict(extra="forbid")

    cells: dict[str, dict[Capability, CapabilityCell]] = Field(default_factory=dict)

    # ----------------------------- loading ------------------------------

    @classmethod
    def load(cls, path: Path) -> CapabilityMatrix:
        """Load a :class:`CapabilityMatrix` from ``capabilities.yaml``.

        Raises:
            FileNotFoundError: ``path`` does not exist.
            ValueError: the YAML shape is not a mapping of employee-id to
                a mapping of capability-key to cell dict.
            ValidationError: a cell fails :class:`CapabilityCell` validation.
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"capability matrix missing: {path}")

        with path.open("r", encoding="utf-8") as fh:
            raw: Any = yaml.safe_load(fh)

        if not isinstance(raw, dict):
            raise ValueError(
                f"{path}: top-level YAML must be a mapping, got {type(raw).__name__}"
            )

        cells: dict[str, dict[Capability, CapabilityCell]] = {}
        for employee_id, row in raw.items():
            if not isinstance(row, dict):
                raise ValueError(
                    f"{path}: employee {employee_id!r} row must be a mapping"
                )
            row_out: dict[Capability, CapabilityCell] = {}
            for cap_key, cell_raw in row.items():
                try:
                    cap = Capability(cap_key)
                except ValueError as exc:
                    raise ValueError(
                        f"{path}: employee {employee_id!r} has unknown "
                        f"capability {cap_key!r}"
                    ) from exc
                if not isinstance(cell_raw, dict):
                    raise ValueError(
                        f"{path}: employee {employee_id!r} capability "
                        f"{cap_key!r} must be a mapping with decision+rationale"
                    )
                try:
                    row_out[cap] = CapabilityCell.model_validate(cell_raw)
                except ValidationError:
                    raise
            cells[str(employee_id)] = row_out

        return cls(cells=cells)

    # ----------------------------- runtime ------------------------------

    def check(self, employee_id: str, capability: Capability) -> Decision:
        """Return the :class:`Decision` for a single (employee, capability).

        Unknown employee id or unmapped capability both default to
        :attr:`Decision.DENY` so the runtime is closed-by-default.
        """
        row = self.cells.get(employee_id)
        if row is None:
            return Decision.DENY
        cell = row.get(capability)
        if cell is None:
            return Decision.DENY
        return cell.decision

    def explain(self, employee_id: str, capability: Capability) -> str:
        """Return the rationale string for the cell, for audit output."""
        row = self.cells.get(employee_id)
        if row is None:
            return f"unknown employee {employee_id!r}; default-deny"
        cell = row.get(capability)
        if cell is None:
            return f"capability {capability.value} unmapped for {employee_id}; default-deny"
        return cell.rationale

    # ---------------------------- introspection -------------------------

    def allowed_capabilities(self, employee_id: str) -> set[Capability]:
        """Return the set of capabilities marked ALLOW for the employee."""
        row = self.cells.get(employee_id, {})
        return {cap for cap, cell in row.items() if cell.decision is Decision.ALLOW}

    def employees(self) -> list[str]:
        """Return the ordered list of employee ids present in the matrix."""
        return sorted(self.cells)


__all__ = [
    "Capability",
    "CapabilityCell",
    "CapabilityError",
    "CapabilityMatrix",
    "Decision",
]
