# SPDX-License-Identifier: Apache-2.0
"""Fleet descriptor and employee YAML catalog loader.

Defines the Pydantic schema for `vyasa.yaml` (fleet-level defaults) and
`employees/<id>.yaml` (per-employee instance configuration). Loads both,
applies deep-merge inheritance (fleet defaults -> employee override, employee
wins), and validates the resulting roster (unique ids, resolvable role_keys,
non-empty tool allowlists).

All paths flow through :class:`pathlib.Path`. No prints; the module emits
structured logs via the standard :mod:`logging` module so callers can route
output to the fleet supervisor.
"""

from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

RegistrySource = Literal["vyasa", "graymatter"]
Visibility = Literal["public", "internal", "private"]


class ModelPreference(BaseModel):
    """Preferred inference model + optional fallback for an employee."""

    model_config = ConfigDict(extra="forbid")

    default: str = Field(..., description="Primary model id, provider-prefixed.")
    provider: str = Field(..., description="Provider key, e.g. 'openrouter' or another model gateway.")
    fallback: dict[str, str] | None = Field(
        default=None,
        description="Optional fallback with keys 'model' and 'provider'.",
    )

    @field_validator("fallback")
    @classmethod
    def _check_fallback_shape(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        if v is None:
            return v
        missing = {"model", "provider"} - set(v)
        if missing:
            raise ValueError(f"fallback missing keys: {sorted(missing)}")
        return v


class EmployeeDescriptor(BaseModel):
    """Typed description of one employee instance.

    Mirrors the `employees/<id>.yaml` schema defined in design-02.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=SCHEMA_VERSION, ge=1)
    id: str = Field(..., min_length=1, description="Slug; employee + memory namespace default.")
    display_name: str = Field(..., min_length=1)
    registry_source: RegistrySource = Field(..., description="Which registry owns the prompt.")
    role_key: str = Field(..., min_length=1, description="AgentRole.value in source registry.")
    system_prompt_ref: str = Field(
        ...,
        min_length=1,
        description="Ref like 'graymatter:managing_partner' or 'file:prompts/foo.md'.",
    )
    allowed_tools: list[str] = Field(default_factory=list)
    allowed_plugins: list[str] = Field(default_factory=list)
    allowed_mcp_servers: list[str] = Field(default_factory=list)
    memory_namespace: str = Field(..., min_length=1)
    model_preference: ModelPreference
    temperature: float = Field(0.4, ge=0.0, le=2.0)
    max_turns: int = Field(25, ge=1, le=500)
    max_tokens: int | None = Field(default=None, ge=1)
    routine_file_ref: str | None = None
    messaging_aliases: list[str] = Field(default_factory=list)
    visibility: Visibility = "internal"
    tags: list[str] = Field(default_factory=list)

    @field_validator("id", "memory_namespace")
    @classmethod
    def _slug_shape(cls, v: str) -> str:
        if any(ch.isspace() for ch in v):
            raise ValueError(f"slug must not contain whitespace: {v!r}")
        return v


class FleetConfig(BaseModel):
    """Fleet-level defaults loaded from `vyasa.yaml`.

    Any field set here is deep-merged under every employee file; the employee
    file wins on conflict.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=SCHEMA_VERSION, ge=1)
    fleet_name: str = Field("vyasa-fleet")
    default_model_preference: ModelPreference | None = None
    default_allowed_plugins: list[str] = Field(default_factory=list)
    default_allowed_mcp_servers: list[str] = Field(default_factory=list)
    default_temperature: float = Field(0.4, ge=0.0, le=2.0)
    default_max_turns: int = Field(25, ge=1, le=500)
    memory_namespace_template: str = Field("employees/{id}/")
    messaging: dict[str, Any] = Field(default_factory=dict)
    tool_delay_ms: int = Field(0, ge=0)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge two mappings. `override` wins on every conflict.

    Lists and scalars are replaced wholesale; only dicts merge recursively.
    """
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _fleet_defaults_as_employee_overlay(fleet: FleetConfig) -> dict[str, Any]:
    """Project `FleetConfig` fields onto the per-employee key-space."""
    overlay: dict[str, Any] = {"schema_version": fleet.schema_version}
    if fleet.default_model_preference is not None:
        overlay["model_preference"] = fleet.default_model_preference.model_dump()
    if fleet.default_allowed_plugins:
        overlay["allowed_plugins"] = list(fleet.default_allowed_plugins)
    if fleet.default_allowed_mcp_servers:
        overlay["allowed_mcp_servers"] = list(fleet.default_allowed_mcp_servers)
    overlay["temperature"] = fleet.default_temperature
    overlay["max_turns"] = fleet.default_max_turns
    return overlay


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top-level YAML must be a mapping, got {type(data).__name__}")
    return data


def load_fleet(root: Path) -> tuple[FleetConfig, list[EmployeeDescriptor]]:
    """Read `vyasa.yaml` + every `employees/*.yaml` under `root`.

    Returns the parsed :class:`FleetConfig` and the list of merged
    :class:`EmployeeDescriptor` objects.

    Raises:
        FileNotFoundError: `root/vyasa.yaml` does not exist.
        ValidationError: any descriptor fails schema validation.
        ValueError: roster-level consistency check fails.
    """
    root = Path(root)
    fleet_path = root / "vyasa.yaml"
    if not fleet_path.exists():
        raise FileNotFoundError(f"fleet config missing: {fleet_path}")

    fleet_raw = _load_yaml(fleet_path)
    fleet = FleetConfig.model_validate(fleet_raw)

    overlay = _fleet_defaults_as_employee_overlay(fleet)

    employees_dir = root / "employees"
    if not employees_dir.exists():
        raise FileNotFoundError(f"employees directory missing: {employees_dir}")

    descriptors: list[EmployeeDescriptor] = []
    for path in sorted(employees_dir.glob("*.yaml")):
        raw = _load_yaml(path)
        merged = _deep_merge(overlay, raw)
        try:
            descriptor = EmployeeDescriptor.model_validate(merged)
        except ValidationError:
            logger.error("employee descriptor failed validation: %s", path)
            raise
        if descriptor.schema_version != fleet.schema_version:
            raise ValueError(
                f"{path}: schema_version {descriptor.schema_version} != fleet "
                f"{fleet.schema_version}"
            )
        descriptors.append(descriptor)

    validate_roster(descriptors)
    logger.info("loaded fleet %s with %d employees", fleet.fleet_name, len(descriptors))
    return fleet, descriptors


def validate_roster(descriptors: list[EmployeeDescriptor]) -> None:
    """Run cross-descriptor consistency checks.

    Raises :class:`ValueError` on any violation.
    """
    seen_ids: set[str] = set()
    seen_aliases: set[str] = set()

    for descriptor in descriptors:
        if descriptor.id in seen_ids:
            raise ValueError(f"duplicate employee id: {descriptor.id}")
        seen_ids.add(descriptor.id)

        if not descriptor.allowed_tools:
            raise ValueError(f"employee {descriptor.id}: allowed_tools must not be empty")

        if ":" not in descriptor.system_prompt_ref:
            raise ValueError(
                f"employee {descriptor.id}: system_prompt_ref must be "
                f"'<namespace>:<key>' or 'file:<path>'"
            )

        ref_namespace = descriptor.system_prompt_ref.split(":", 1)[0]
        if ref_namespace not in {"graymatter", "vyasa", "file"}:
            raise ValueError(
                f"employee {descriptor.id}: unknown prompt namespace {ref_namespace!r}"
            )

        for alias in descriptor.messaging_aliases:
            if alias in seen_aliases:
                raise ValueError(
                    f"employee {descriptor.id}: alias {alias!r} already claimed"
                )
            seen_aliases.add(alias)


__all__ = [
    "SCHEMA_VERSION",
    "EmployeeDescriptor",
    "FleetConfig",
    "ModelPreference",
    "load_fleet",
    "validate_roster",
]
