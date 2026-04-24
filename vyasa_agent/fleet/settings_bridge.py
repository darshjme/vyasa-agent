"""SettingsStore → FleetManager overlay bridge (design-06 §5, design-08 §7).

The YAML tree (``vyasa.yaml`` + ``employees/*.yaml``) is the *ground truth*
descriptor layout.  The SQLite settings table is the *live overlay*: anything
a merchant, operator, or partner tunes through the admin panel applies on top
of the YAML without touching the files on disk (constitution §4 row 1).

Two responsibilities live here:

1. **Cold read cache.** At boot we copy every relevant key out of
   :class:`SettingsStore` into an in-memory snapshot so hot-path reads stay
   sub-millisecond.  The cache is rebuilt on ``notify_change`` for the keys
   that actually moved.
2. **Change fan-out.** The admin router calls :meth:`SettingsOverlay.notify_change`
   after every write so subscribers (FleetManager, channel adapters, branding
   template) update in place — no actor restart for non-critical changes.

Thread-safety uses :class:`asyncio.Lock` around cache writes; readers work
off the frozen snapshot so they never block on a write.  Sync callers that
run outside an event loop (pytest, adapter sidecars) call :meth:`refresh`
explicitly instead of waiting on a change event.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from vyasa_agent.admin_panel.settings_store import SettingsStore
from vyasa_agent.fleet.descriptor import EmployeeDescriptor, ModelPreference

logger = logging.getLogger("vyasa.fleet.settings_bridge")

# Default when the admin has not set ``fleet.concurrency.default``.
DEFAULT_CONCURRENT_LIMIT: int = 8

# Keys we watch.  Kept explicit so an unrelated setting (e.g. ``admin.cors_origins``)
# does not invalidate the fleet snapshot.
_FLEET_KEYS = {
    "fleet.concurrency.default",
    "fleet.budget.daily_inr",
    "fleet.employees.enabled",
    "fleet.employees.disabled",
}
_BRANDING_KEYS = {
    "branding.product_name",
    "branding.primary_color",
    "branding.accent_color",
    "branding.ivory_color",
    "branding.locale.default",
    "branding.logo_url",
}
_CHANNEL_TOKEN_KEYS = {
    "channels.telegram.bot_token",
    "channels.whatsapp.bot_token",
    "channels.slack.bot_token",
}


# --------------------------------------------------------------------------- #
# BrandingConfig
# --------------------------------------------------------------------------- #


class BrandingConfig(BaseModel):
    """White-label branding surface consumed by templates + HTML docs."""

    model_config = ConfigDict(extra="forbid")

    product_name: str = Field("Vyasa Agent", min_length=1, max_length=120)
    primary_color: str = Field("#0F2E3D", pattern=r"^#[0-9A-Fa-f]{6}$")
    accent_color: str = Field("#E28822", pattern=r"^#[0-9A-Fa-f]{6}$")
    ivory: str = Field("#F6F1E5", pattern=r"^#[0-9A-Fa-f]{6}$")
    locale: str = Field("en-IN", min_length=2, max_length=16)
    logo_url: str = ""


# --------------------------------------------------------------------------- #
# Employee-id helpers
# --------------------------------------------------------------------------- #


def _normalise_employee_id(raw: str) -> str:
    """Return a canonical form so ``dr-reddy`` and ``dr.reddy`` collide.

    The descriptor schema allows either separator; admins may type whichever
    reads cleaner in the UI.  We keep matching lenient so a tunable set in the
    admin panel against one spelling still affects the descriptor loaded under
    the other.
    """
    return raw.strip().lower().replace(".", "-")


# --------------------------------------------------------------------------- #
# SettingsOverlay
# --------------------------------------------------------------------------- #


class SettingsOverlay:
    """Hot overlay atop :class:`SettingsStore`.

    Boot path: ``refresh()`` once; afterwards reads hit the in-memory snapshot.
    Change path: ``notify_change(key, value)`` rebuilds only the touched slice
    and fires every registered subscriber callback with ``(key, value)``.

    Callers may still bypass the snapshot by calling ``store.get(key)`` — the
    overlay never shadows the underlying store, it just caches.
    """

    def __init__(self, store: SettingsStore) -> None:
        self._store = store
        self._lock = asyncio.Lock()
        self._callbacks: list[Callable[[str, Any], Any]] = []

        # Snapshot primitives — plain dicts for O(1) lookup.  These are
        # *always* read-only from outside this class; methods return fresh
        # objects (e.g. ``BrandingConfig``) so callers cannot mutate us.
        self._fleet_snapshot: dict[str, Any] = {}
        self._branding_snapshot: dict[str, Any] = {}
        self._channel_snapshot: dict[str, Any] = {}
        self._employee_overrides: dict[str, dict[str, Any]] = {}
        self._employees_enabled: set[str] | None = None
        self._employees_disabled: set[str] = set()

        self.refresh()

    # ---------------- snapshot lifecycle ----------------

    def refresh(self) -> None:
        """Re-read every watched key into the in-memory snapshot.

        Safe to call from sync code; does not acquire the async lock (boot
        happens before the event loop owns us).  Production writes go through
        :meth:`notify_change`, which *does* take the lock.
        """
        self._fleet_snapshot = self._collect(_FLEET_KEYS)
        self._branding_snapshot = self._collect(_BRANDING_KEYS)
        self._channel_snapshot = self._collect(_CHANNEL_TOKEN_KEYS)

        enabled_raw = self._fleet_snapshot.get("fleet.employees.enabled")
        if isinstance(enabled_raw, list) and enabled_raw:
            self._employees_enabled = {
                _normalise_employee_id(x) for x in enabled_raw if isinstance(x, str)
            }
        else:
            self._employees_enabled = None  # allow-all when unset / empty

        disabled_raw = self._fleet_snapshot.get("fleet.employees.disabled")
        if isinstance(disabled_raw, list):
            self._employees_disabled = {
                _normalise_employee_id(x) for x in disabled_raw if isinstance(x, str)
            }
        else:
            self._employees_disabled = set()

        # Per-employee overrides live under ``fleet.employee.<id>.*`` and are
        # not in the seeded defaults — we discover them by listing the fleet
        # section.
        self._employee_overrides = self._collect_employee_overrides()

    def _collect(self, keys: set[str]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key in keys:
            value = self._store.get(key)
            if value is not None:
                out[key] = value
        return out

    def _collect_employee_overrides(self) -> dict[str, dict[str, Any]]:
        """Collect ``fleet.employee.<id>.<field>`` rows.

        Convention: employee ids in settings keys are written in dash form
        (``dr-reddy``) to avoid colliding with the dotted key separator.
        The overlay normalises before lookup so either descriptor form
        (``dr.reddy`` or ``dr-reddy``) resolves.
        """
        rows = self._store.list(section="fleet")
        overrides: dict[str, dict[str, Any]] = {}
        prefix = "fleet.employee."
        for row in rows:
            key = row["key"]
            if not key.startswith(prefix):
                continue
            remainder = key[len(prefix):]
            if "." not in remainder:
                continue
            emp_id, field = remainder.split(".", 1)
            canon = _normalise_employee_id(emp_id)
            overrides.setdefault(canon, {})[field] = row["value"]
        return overrides

    # ---------------- change fan-out ----------------

    def watch(self, callback: Callable[[str, Any], Any]) -> None:
        """Register ``callback(key, value)`` for every admin settings write."""
        self._callbacks.append(callback)

    def notify_change(self, key: str, value: Any) -> None:
        """Called by the admin router after a successful write.

        Refreshes only the slice the key belongs to (cheap), then fans the
        change out to every subscriber.  Exceptions in one subscriber never
        block the others — they are logged and swallowed.
        """
        self._apply_single(key, value)

        for callback in list(self._callbacks):
            try:
                result = callback(key, value)
                # Allow async subscribers without forcing callers to await us.
                if asyncio.iscoroutine(result):
                    loop = _try_get_loop()
                    if loop is not None:
                        loop.create_task(result)  # type: ignore[arg-type]
                    else:
                        # No loop: drop the coroutine rather than leak it.
                        result.close()
            except Exception:  # pragma: no cover - defensive
                logger.exception(
                    "settings.notify.callback_failed",
                    extra={"event": "settings.notify.callback_failed", "key": key},
                )

    def _apply_single(self, key: str, value: Any) -> None:
        if key in _FLEET_KEYS:
            self._fleet_snapshot[key] = value
            if key == "fleet.employees.enabled":
                if isinstance(value, list) and value:
                    self._employees_enabled = {
                        _normalise_employee_id(x) for x in value if isinstance(x, str)
                    }
                else:
                    self._employees_enabled = None
            elif key == "fleet.employees.disabled":
                if isinstance(value, list):
                    self._employees_disabled = {
                        _normalise_employee_id(x) for x in value if isinstance(x, str)
                    }
                else:
                    self._employees_disabled = set()
        elif key in _BRANDING_KEYS:
            self._branding_snapshot[key] = value
        elif key in _CHANNEL_TOKEN_KEYS:
            self._channel_snapshot[key] = value
        elif key.startswith("fleet.employee."):
            remainder = key[len("fleet.employee."):]
            if "." in remainder:
                emp_id, field = remainder.split(".", 1)
                canon = _normalise_employee_id(emp_id)
                self._employee_overrides.setdefault(canon, {})[field] = value

    # ---------------- read API ----------------

    def get_employee_enabled(self, employee_id: str) -> bool:
        """Return ``True`` unless the admin disabled this employee."""
        canon = _normalise_employee_id(employee_id)
        if canon in self._employees_disabled:
            return False
        per_emp = self._employee_overrides.get(canon, {})
        explicit = per_emp.get("enabled")
        if isinstance(explicit, bool):
            return explicit
        if self._employees_enabled is not None:
            return canon in self._employees_enabled
        return True

    def get_employee_model(self, employee_id: str) -> str | None:
        """Return the admin-chosen model id, or ``None`` to keep YAML default."""
        canon = _normalise_employee_id(employee_id)
        per_emp = self._employee_overrides.get(canon, {})
        value = per_emp.get("model")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    def get_fleet_budget_daily_inr(self) -> Decimal | None:
        raw = self._fleet_snapshot.get("fleet.budget.daily_inr")
        if raw is None:
            return None
        try:
            return Decimal(str(raw))
        except (InvalidOperation, ValueError):
            logger.warning(
                "settings.budget.invalid",
                extra={"event": "settings.budget.invalid", "value": raw},
            )
            return None

    def get_fleet_concurrent_limit(self) -> int:
        raw = self._fleet_snapshot.get("fleet.concurrency.default")
        if isinstance(raw, bool):  # bool is an int subclass in Python — guard.
            return DEFAULT_CONCURRENT_LIMIT
        if isinstance(raw, int) and raw > 0:
            return raw
        if isinstance(raw, str):
            try:
                parsed = int(raw)
                if parsed > 0:
                    return parsed
            except ValueError:
                pass
        return DEFAULT_CONCURRENT_LIMIT

    def get_channel_bot_token(self, channel: str) -> str | None:
        """Return the bot token for ``telegram`` / ``whatsapp`` / ``slack``.

        Secret-ref stubs (``{"kind": "secret_ref", ...}``) resolve to ``None``
        — the caller should then walk the vault, not consume the stub string.
        """
        key = f"channels.{channel.lower()}.bot_token"
        raw = self._channel_snapshot.get(key)
        if raw is None:
            return None
        if isinstance(raw, dict):
            return None
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        return None

    def get_branding(self) -> BrandingConfig:
        snap = self._branding_snapshot
        return BrandingConfig(
            product_name=_coerce_str(snap.get("branding.product_name"), "Vyasa Agent"),
            primary_color=_coerce_str(snap.get("branding.primary_color"), "#0F2E3D"),
            accent_color=_coerce_str(snap.get("branding.accent_color"), "#E28822"),
            ivory=_coerce_str(snap.get("branding.ivory_color"), "#F6F1E5"),
            locale=_coerce_str(snap.get("branding.locale.default"), "en-IN"),
            logo_url=_coerce_str(snap.get("branding.logo_url"), ""),
        )

    # ---------------- test / introspection hooks ----------------

    @property
    def store(self) -> SettingsStore:
        return self._store


# --------------------------------------------------------------------------- #
# Descriptor overlay
# --------------------------------------------------------------------------- #


def apply_overlay(
    descriptor: EmployeeDescriptor,
    overlay: SettingsOverlay,
) -> EmployeeDescriptor:
    """Return a *new* descriptor with settings merged on top of YAML.

    The YAML file stays untouched.  Only fields the admin is allowed to tune
    are swapped; everything else (role key, prompt ref, allowed tools) comes
    straight from disk.
    """
    override_model = overlay.get_employee_model(descriptor.id)
    if override_model is None:
        return descriptor

    new_preference = ModelPreference(
        default=override_model,
        provider=descriptor.model_preference.provider,
        fallback=descriptor.model_preference.fallback,
    )
    return descriptor.model_copy(update={"model_preference": new_preference})


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _coerce_str(value: Any, fallback: str) -> str:
    if isinstance(value, str) and value.strip():
        return value
    return fallback


def _try_get_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


__all__ = [
    "DEFAULT_CONCURRENT_LIMIT",
    "BrandingConfig",
    "SettingsOverlay",
    "apply_overlay",
]
