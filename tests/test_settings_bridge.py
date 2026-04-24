"""SettingsOverlay + FleetManager bridge tests (design-06 §5, design-08 §7).

Covers:

* Overlay loads seeded defaults (concurrency, branding).
* Admin override of ``fleet.concurrency.default`` flows into the overlay.
* Disabling an employee via settings makes ``FleetManager.dispatch`` return a
  polite unavailable :class:`TurnResult`; re-enabling restores normal work.
* Per-employee enabled flips via ``fleet.employee.<id>.enabled`` also route.
* Branding primary-color change is reflected in ``overlay.get_branding()``.
* Overlay ``watch()`` subscribers receive the change.

The descriptor layer is synthesised in-test (no dependency on
``~/.vyasa/employees/*.yaml``) so the suite runs in a sealed environment.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from vyasa_agent.admin_panel.seeds import DEFAULTS
from vyasa_agent.admin_panel.settings_store import SettingsStore
from vyasa_agent.fleet.actor import EmployeeActor
from vyasa_agent.fleet.descriptor import EmployeeDescriptor, FleetConfig, ModelPreference
from vyasa_agent.fleet.manager import FleetManager
from vyasa_agent.fleet.settings_bridge import (
    DEFAULT_CONCURRENT_LIMIT,
    BrandingConfig,
    SettingsOverlay,
    apply_overlay,
)
from vyasa_agent.fleet.types import Turn


def _mk_descriptor(employee_id: str, display_name: str) -> EmployeeDescriptor:
    return EmployeeDescriptor(
        id=employee_id,
        display_name=display_name,
        registry_source="vyasa",
        role_key=employee_id.replace("-", "_").replace(".", "_"),
        system_prompt_ref=f"vyasa:{employee_id}",
        allowed_tools=["bash"],
        memory_namespace=employee_id,
        model_preference=ModelPreference(
            default="openrouter/anthropic/haiku-4-5",
            provider="openrouter",
        ),
    )


def _mk_fleet_config() -> FleetConfig:
    return FleetConfig(fleet_name="vyasa-fleet-test")


@pytest.fixture
def seeded_store() -> SettingsStore:
    store = SettingsStore(":memory:")
    store.seed_defaults(DEFAULTS, actor="system")
    yield store
    store.close()


# --------------------------------------------------------------------------- #
# Overlay unit tests — run sync, no event loop.
# --------------------------------------------------------------------------- #


def test_overlay_loads_seeded_defaults(seeded_store: SettingsStore) -> None:
    overlay = SettingsOverlay(seeded_store)

    branding = overlay.get_branding()
    assert isinstance(branding, BrandingConfig)
    assert branding.product_name == "Vyasa Agent"
    assert branding.primary_color == "#0F2E3D"
    assert branding.accent_color == "#E28822"
    assert branding.ivory == "#F6F1E5"
    assert branding.locale == "en-IN"

    # ``fleet.concurrency.default`` seeds at 4; overlay's fallback kicks in
    # only when the key is missing.
    assert overlay.get_fleet_concurrent_limit() == 4
    # No budget seeded => ``None``.
    assert overlay.get_fleet_budget_daily_inr() is None


def test_overlay_falls_back_when_unseeded() -> None:
    # Empty store: no defaults, no values.
    store = SettingsStore(":memory:")
    try:
        overlay = SettingsOverlay(store)
        assert overlay.get_fleet_concurrent_limit() == DEFAULT_CONCURRENT_LIMIT
        assert overlay.get_employee_enabled("anyone") is True
        assert overlay.get_channel_bot_token("telegram") is None
        # Branding returns sensible defaults even with no rows.
        branding = overlay.get_branding()
        assert branding.product_name == "Vyasa Agent"
    finally:
        store.close()


def test_overlay_reflects_concurrency_override(seeded_store: SettingsStore) -> None:
    overlay = SettingsOverlay(seeded_store)
    assert overlay.get_fleet_concurrent_limit() == 4

    seeded_store.set("fleet.concurrency.default", 32, user="admin", section="fleet")
    overlay.notify_change("fleet.concurrency.default", 32)
    assert overlay.get_fleet_concurrent_limit() == 32


def test_overlay_branding_color_change(seeded_store: SettingsStore) -> None:
    overlay = SettingsOverlay(seeded_store)
    assert overlay.get_branding().primary_color == "#0F2E3D"

    seeded_store.set(
        "branding.primary_color", "#123456", user="admin", section="branding"
    )
    overlay.notify_change("branding.primary_color", "#123456")
    assert overlay.get_branding().primary_color == "#123456"


def test_overlay_watch_callback_fires(seeded_store: SettingsStore) -> None:
    overlay = SettingsOverlay(seeded_store)
    seen: list[tuple[str, object]] = []
    overlay.watch(lambda k, v: seen.append((k, v)))

    overlay.notify_change("branding.product_name", "Renamed")
    assert seen == [("branding.product_name", "Renamed")]


def test_overlay_budget_parses_decimal(seeded_store: SettingsStore) -> None:
    seeded_store.set("fleet.budget.daily_inr", "1200.50", user="admin", section="fleet")
    overlay = SettingsOverlay(seeded_store)
    value = overlay.get_fleet_budget_daily_inr()
    assert value == Decimal("1200.50")


def test_overlay_per_employee_model_override(seeded_store: SettingsStore) -> None:
    seeded_store.set(
        "fleet.employee.dr-reddy.model",
        "openrouter/anthropic/opus-4-7",
        user="admin",
        section="fleet",
    )
    overlay = SettingsOverlay(seeded_store)
    assert overlay.get_employee_model("dr-reddy") == "openrouter/anthropic/opus-4-7"
    # Dotted form normalises to the same record.
    assert overlay.get_employee_model("dr.reddy") == "openrouter/anthropic/opus-4-7"
    assert overlay.get_employee_model("vyasa") is None


def test_apply_overlay_swaps_model(seeded_store: SettingsStore) -> None:
    seeded_store.set(
        "fleet.employee.dr-reddy.model",
        "openrouter/anthropic/opus-4-7",
        user="admin",
        section="fleet",
    )
    overlay = SettingsOverlay(seeded_store)

    descriptor = _mk_descriptor("dr-reddy", "Dr. Meera Reddy")
    merged = apply_overlay(descriptor, overlay)
    assert merged.model_preference.default == "openrouter/anthropic/opus-4-7"
    # Provider is preserved from the YAML — admins do not retune providers.
    assert merged.model_preference.provider == descriptor.model_preference.provider
    # Original descriptor is untouched (immutability).
    assert descriptor.model_preference.default == "openrouter/anthropic/haiku-4-5"


def test_apply_overlay_noop_without_override(seeded_store: SettingsStore) -> None:
    overlay = SettingsOverlay(seeded_store)
    descriptor = _mk_descriptor("vyasa", "Vyasa")
    merged = apply_overlay(descriptor, overlay)
    # No override => same descriptor object identity semantics preserved.
    assert merged.model_preference.default == descriptor.model_preference.default


# --------------------------------------------------------------------------- #
# FleetManager integration — disabled / re-enabled via overlay.
# --------------------------------------------------------------------------- #


async def test_fleet_dispatch_unavailable_when_disabled(
    tmp_path: Path, seeded_store: SettingsStore
) -> None:
    overlay = SettingsOverlay(seeded_store)
    manager = FleetManager()
    try:
        descriptor = _mk_descriptor("dr-reddy", "Dr. Meera Reddy")
        actor = EmployeeActor(
            descriptor, _mk_fleet_config(), state_root=tmp_path / "state"
        )
        await manager.register_actor(actor)
        manager.attach_overlay(overlay)

        # Baseline: normal dispatch works.
        pre = await manager.dispatch(
            "dr-reddy", Turn(text="ping", employee_id="dr-reddy")
        )
        assert pre.error is None
        assert "gnip" in pre.text  # stub reversal

        # Admin disables dr-reddy.
        seeded_store.set(
            "fleet.employees.disabled",
            ["dr-reddy"],
            user="admin",
            section="fleet",
        )
        overlay.notify_change("fleet.employees.disabled", ["dr-reddy"])

        mid = await manager.dispatch(
            "dr-reddy", Turn(text="ping", employee_id="dr-reddy")
        )
        assert mid.error == "employee_disabled"
        assert "unavailable" in mid.text.lower()
        assert mid.confidence_score == 0.0

        # Admin re-enables by clearing the disabled list.
        seeded_store.set(
            "fleet.employees.disabled", [], user="admin", section="fleet"
        )
        overlay.notify_change("fleet.employees.disabled", [])

        post = await manager.dispatch(
            "dr-reddy", Turn(text="ping", employee_id="dr-reddy")
        )
        assert post.error is None
        assert "gnip" in post.text
    finally:
        await manager.shutdown()


async def test_fleet_per_employee_enabled_flip(
    tmp_path: Path, seeded_store: SettingsStore
) -> None:
    overlay = SettingsOverlay(seeded_store)
    manager = FleetManager()
    try:
        actor = EmployeeActor(
            _mk_descriptor("dr-reddy", "Dr. Meera Reddy"),
            _mk_fleet_config(),
            state_root=tmp_path / "state",
        )
        await manager.register_actor(actor)
        manager.attach_overlay(overlay)

        overlay.notify_change("fleet.employee.dr-reddy.enabled", False)
        disabled = await manager.dispatch(
            "dr-reddy", Turn(text="ping", employee_id="dr-reddy")
        )
        assert disabled.error == "employee_disabled"

        overlay.notify_change("fleet.employee.dr-reddy.enabled", True)
        enabled = await manager.dispatch(
            "dr-reddy", Turn(text="ping", employee_id="dr-reddy")
        )
        assert enabled.error is None
    finally:
        await manager.shutdown()


async def test_fleet_allowlist_enabled_semantics(
    tmp_path: Path, seeded_store: SettingsStore
) -> None:
    overlay = SettingsOverlay(seeded_store)
    manager = FleetManager()
    try:
        for descriptor in (
            _mk_descriptor("dr-reddy", "Dr. Meera Reddy"),
            _mk_descriptor("vyasa", "Vyasa"),
        ):
            actor = EmployeeActor(
                descriptor, _mk_fleet_config(), state_root=tmp_path / "state"
            )
            await manager.register_actor(actor)
        manager.attach_overlay(overlay)

        # Allowlist only dr-reddy — vyasa must become unavailable.
        overlay.notify_change("fleet.employees.enabled", ["dr-reddy"])
        reddy = await manager.dispatch(
            "dr-reddy", Turn(text="ping", employee_id="dr-reddy")
        )
        vyasa = await manager.dispatch(
            "vyasa", Turn(text="ping", employee_id="vyasa")
        )
        assert reddy.error is None
        assert vyasa.error == "employee_disabled"

        # Empty list => allow everyone again.
        overlay.notify_change("fleet.employees.enabled", [])
        vyasa_restored = await manager.dispatch(
            "vyasa", Turn(text="ping", employee_id="vyasa")
        )
        assert vyasa_restored.error is None
    finally:
        await manager.shutdown()
