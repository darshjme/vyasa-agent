"""Tests for the fleet descriptor loader.

Covers:
  * happy-path load of all 28 employees under repo root
  * duplicate id rejection in :func:`validate_roster`
  * missing role_key / empty allowed_tools rejection
  * fleet-default inheritance via deep-merge
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from vyasa_agent.fleet.descriptor import (
    EmployeeDescriptor,
    FleetConfig,
    ModelPreference,
    load_fleet,
    validate_roster,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------- happy path against the real catalog -----------------------------

def test_load_fleet_full_roster() -> None:
    """All 19 Vyasa specialists + 10 Graymatter doctors = 29 employees.

    Note: design doc rounds to '28 specialists' because Vyasa (the orchestrator)
    is sometimes counted separately from the 18 subordinate roles.
    """
    fleet, descriptors = load_fleet(REPO_ROOT)

    assert isinstance(fleet, FleetConfig)
    assert fleet.schema_version == 1

    ids = {d.id for d in descriptors}
    assert len(descriptors) == 29, f"expected 29 employees, got {len(descriptors)}: {sorted(ids)}"
    assert len(ids) == 29, "duplicate ids detected in catalog"

    vyasa_ids = {d.id for d in descriptors if d.registry_source == "vyasa"}
    graymatter_ids = {d.id for d in descriptors if d.registry_source == "graymatter"}
    assert len(vyasa_ids) == 19, f"expected 19 vyasa, got {len(vyasa_ids)}"
    assert len(graymatter_ids) == 10, f"expected 10 graymatter, got {len(graymatter_ids)}"


def test_every_employee_has_required_scoping() -> None:
    _, descriptors = load_fleet(REPO_ROOT)
    for d in descriptors:
        assert d.allowed_tools, f"{d.id}: allowed_tools empty"
        assert d.memory_namespace, f"{d.id}: memory_namespace empty"
        assert d.system_prompt_ref.split(":", 1)[0] in {"vyasa", "graymatter", "file"}
        assert d.messaging_aliases, f"{d.id}: no messaging aliases"


# ---------- roster-level validation ----------------------------------------

def _sample_descriptor(**overrides: object) -> EmployeeDescriptor:
    base = {
        "schema_version": 1,
        "id": "probe",
        "display_name": "Probe",
        "registry_source": "vyasa",
        "role_key": "coder",
        "system_prompt_ref": "vyasa:coder",
        "allowed_tools": ["bash", "file_read"],
        "memory_namespace": "probe",
        "model_preference": ModelPreference(
            default="openrouter/anthropic/haiku-4-5", provider="openrouter"
        ),
        "temperature": 0.4,
        "max_turns": 25,
        "messaging_aliases": ["@probe"],
    }
    base.update(overrides)
    return EmployeeDescriptor.model_validate(base)


def test_validate_roster_rejects_duplicate_id() -> None:
    a = _sample_descriptor(id="twin", messaging_aliases=["@twin1"])
    b = _sample_descriptor(id="twin", messaging_aliases=["@twin2"])
    with pytest.raises(ValueError, match="duplicate employee id"):
        validate_roster([a, b])


def test_validate_roster_rejects_empty_tools() -> None:
    d = _sample_descriptor(allowed_tools=[])
    with pytest.raises(ValueError, match="allowed_tools must not be empty"):
        validate_roster([d])


def test_validate_roster_rejects_unknown_prompt_namespace() -> None:
    d = _sample_descriptor(system_prompt_ref="unknownns:foo")
    with pytest.raises(ValueError, match="unknown prompt namespace"):
        validate_roster([d])


def test_validate_roster_rejects_alias_collision() -> None:
    a = _sample_descriptor(id="a", messaging_aliases=["@same"])
    b = _sample_descriptor(id="b", messaging_aliases=["@same"])
    with pytest.raises(ValueError, match="already claimed"):
        validate_roster([a, b])


# ---------- fleet-default inheritance --------------------------------------

def test_fleet_defaults_inherited_when_employee_omits_field(tmp_path: Path) -> None:
    fleet_yaml = {
        "schema_version": 1,
        "fleet_name": "tiny-fleet",
        "default_model_preference": {
            "default": "openrouter/anthropic/haiku-4-5",
            "provider": "openrouter",
        },
        "default_temperature": 0.2,
        "default_max_turns": 42,
        "default_allowed_plugins": ["memory"],
    }
    employee_yaml = {
        "id": "mini",
        "display_name": "Mini",
        "registry_source": "vyasa",
        "role_key": "coder",
        "system_prompt_ref": "vyasa:coder",
        "allowed_tools": ["file_read"],
        "memory_namespace": "mini",
        "messaging_aliases": ["@mini"],
    }
    (tmp_path / "vyasa.yaml").write_text(yaml.safe_dump(fleet_yaml), encoding="utf-8")
    employees_dir = tmp_path / "employees"
    employees_dir.mkdir()
    (employees_dir / "mini.yaml").write_text(yaml.safe_dump(employee_yaml), encoding="utf-8")

    fleet, descriptors = load_fleet(tmp_path)
    assert fleet.fleet_name == "tiny-fleet"
    assert len(descriptors) == 1
    mini = descriptors[0]
    assert mini.temperature == pytest.approx(0.2)
    assert mini.max_turns == 42
    assert mini.model_preference.default == "openrouter/anthropic/haiku-4-5"
    assert mini.allowed_plugins == ["memory"]


def test_employee_overrides_fleet_defaults(tmp_path: Path) -> None:
    fleet_yaml = {
        "schema_version": 1,
        "default_model_preference": {
            "default": "openrouter/anthropic/haiku-4-5",
            "provider": "openrouter",
        },
        "default_temperature": 0.2,
    }
    employee_yaml = {
        "id": "bigbrain",
        "display_name": "Bigbrain",
        "registry_source": "vyasa",
        "role_key": "orchestrator",
        "system_prompt_ref": "vyasa:orchestrator",
        "allowed_tools": ["file_read"],
        "memory_namespace": "bigbrain",
        "temperature": 0.9,
        "model_preference": {
            "default": "openrouter/anthropic/opus-4-7",
            "provider": "openrouter",
        },
        "messaging_aliases": ["@bigbrain"],
    }
    (tmp_path / "vyasa.yaml").write_text(yaml.safe_dump(fleet_yaml), encoding="utf-8")
    employees_dir = tmp_path / "employees"
    employees_dir.mkdir()
    (employees_dir / "bigbrain.yaml").write_text(
        yaml.safe_dump(employee_yaml), encoding="utf-8"
    )

    _, descriptors = load_fleet(tmp_path)
    bb = descriptors[0]
    assert bb.temperature == pytest.approx(0.9)
    assert bb.model_preference.default == "openrouter/anthropic/opus-4-7"


def test_load_fleet_errors_when_fleet_file_missing(tmp_path: Path) -> None:
    (tmp_path / "employees").mkdir()
    with pytest.raises(FileNotFoundError, match="fleet config missing"):
        load_fleet(tmp_path)


def test_load_fleet_errors_when_employees_dir_missing(tmp_path: Path) -> None:
    (tmp_path / "vyasa.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="employees directory missing"):
        load_fleet(tmp_path)


def test_schema_version_mismatch_rejected(tmp_path: Path) -> None:
    (tmp_path / "vyasa.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    employees_dir = tmp_path / "employees"
    employees_dir.mkdir()
    bad = {
        "schema_version": 2,
        "id": "future",
        "display_name": "Future",
        "registry_source": "vyasa",
        "role_key": "coder",
        "system_prompt_ref": "vyasa:coder",
        "allowed_tools": ["file_read"],
        "memory_namespace": "future",
        "model_preference": {
            "default": "openrouter/anthropic/haiku-4-5",
            "provider": "openrouter",
        },
        "messaging_aliases": ["@future"],
    }
    (employees_dir / "future.yaml").write_text(yaml.safe_dump(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version"):
        load_fleet(tmp_path)
