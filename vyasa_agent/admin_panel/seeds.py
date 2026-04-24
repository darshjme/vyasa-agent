"""Default settings seeded on first boot (design-08 §7, design-06 §5).

Every tunable visible in the Admin Panel lives here. Five sections:

* ``fleet``       — employee roster toggles, routing defaults, per-employee
                    model and budget knobs.
* ``channels``    — adapter bot-tokens, allowlists, webhook routes. Secrets
                    are written as ``secret_ref`` stubs, never raw.
* ``memory``      — retention, compaction cadence, graph staleness.
* ``integrations``— Envato, Stripe, cloud provider.
* ``branding``    — product name, colors, typography, locale toggles.

The branding defaults encode the Vyasa palette (midnight teal + saffron +
ivory) and the ``en-IN`` locale.  No provider names leak into any value.
"""

from __future__ import annotations

from typing import Any


def _secret_ref(key: str) -> dict[str, str]:
    return {"kind": "secret_ref", "ref": f"vault://settings/{key}"}


FLEET_DEFAULTS: list[dict[str, Any]] = [
    {
        "key": "fleet.routing.default_employee",
        "section": "fleet",
        "value": "vyasa",
        "schema": {"type": "string"},
    },
    {
        "key": "fleet.routing.product_orchestrator",
        "section": "fleet",
        "value": "dr-sarabhai",
        "schema": {"type": "string"},
    },
    {
        "key": "fleet.employees.enabled",
        "section": "fleet",
        "value": [],
        "schema": {"type": "array", "items": {"type": "string"}},
    },
    {
        "key": "fleet.concurrency.default",
        "section": "fleet",
        "value": 4,
        "schema": {"type": "integer", "minimum": 1, "maximum": 64},
    },
]


CHANNELS_DEFAULTS: list[dict[str, Any]] = [
    {
        "key": "channels.telegram.bot_token",
        "section": "channels",
        "value": _secret_ref("channels.telegram.bot_token"),
        "schema": {"type": "secret_ref"},
    },
    {
        "key": "channels.telegram.allowlist_chat_ids",
        "section": "channels",
        "value": [],
        "schema": {"type": "array", "items": {"type": "string"}},
    },
    {
        "key": "channels.whatsapp.allowlist_numbers",
        "section": "channels",
        "value": [],
        "schema": {"type": "array", "items": {"type": "string"}},
    },
    {
        "key": "channels.webhook.routes",
        "section": "channels",
        "value": [],
        "schema": {"type": "array"},
    },
    {
        "key": "channels.gateway.tokens",
        "section": "channels",
        "value": [],
        "schema": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "token": {"type": "string"},
                    "scope": {"type": "string"},
                    "label": {"type": "string"},
                },
            },
            "description": "Opaque bearer tokens (vya_live_...) issued per adapter.",
        },
    },
]


MEMORY_DEFAULTS: list[dict[str, Any]] = [
    {
        "key": "memory.retention_days",
        "section": "memory",
        "value": 365,
        "schema": {"type": "integer", "minimum": 7, "maximum": 3650},
    },
    {
        "key": "memory.compaction.threshold_tokens",
        "section": "memory",
        "value": 16000,
        "schema": {"type": "integer", "minimum": 1024},
    },
    {
        "key": "memory.compaction.interval_hours",
        "section": "memory",
        "value": 24,
        "schema": {"type": "integer", "minimum": 1, "maximum": 168},
    },
    {
        "key": "memory.graph.stale_after_days",
        "section": "memory",
        "value": 7,
        "schema": {"type": "integer", "minimum": 1, "maximum": 90},
    },
]


INTEGRATIONS_DEFAULTS: list[dict[str, Any]] = [
    {
        "key": "integrations.envato.personal_token",
        "section": "integrations",
        "value": _secret_ref("integrations.envato.personal_token"),
        "schema": {"type": "secret_ref"},
    },
    {
        "key": "integrations.envato.cache_ttl_s",
        "section": "integrations",
        "value": 21600,
        "schema": {"type": "integer", "minimum": 60, "maximum": 604800},
    },
    {
        "key": "integrations.envato.product_codes",
        "section": "integrations",
        "value": [],
        "schema": {"type": "array", "items": {"type": "string"}},
    },
    {
        "key": "integrations.stripe.secret_key",
        "section": "integrations",
        "value": _secret_ref("integrations.stripe.secret_key"),
        "schema": {"type": "secret_ref"},
    },
    {
        "key": "integrations.cloud.provider",
        "section": "integrations",
        "value": "self-hosted",
        "schema": {"type": "string", "enum": ["self-hosted", "aws", "gcp", "hetzner", "fly"]},
    },
]


BRANDING_DEFAULTS: list[dict[str, Any]] = [
    {
        "key": "branding.product_name",
        "section": "branding",
        "value": "Vyasa Agent",
        "schema": {"type": "string", "minLength": 1, "maxLength": 120},
    },
    {
        "key": "branding.logo_url",
        "section": "branding",
        "value": "",
        "schema": {"type": "string", "format": "uri"},
    },
    {
        "key": "branding.primary_color",
        "section": "branding",
        "value": "#0F2E3D",
        "schema": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"},
    },
    {
        "key": "branding.accent_color",
        "section": "branding",
        "value": "#E28822",
        "schema": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"},
    },
    {
        "key": "branding.ivory_color",
        "section": "branding",
        "value": "#F6F1E5",
        "schema": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"},
    },
    {
        "key": "branding.typography.primary",
        "section": "branding",
        "value": "Inter Tight, system-ui, sans-serif",
        "schema": {"type": "string"},
    },
    {
        "key": "branding.white_label.enabled",
        "section": "branding",
        "value": True,
        "schema": {"type": "boolean"},
    },
    {
        "key": "branding.locale.default",
        "section": "branding",
        "value": "en-IN",
        "schema": {"type": "string"},
    },
    {
        "key": "branding.locale.enabled",
        "section": "branding",
        "value": ["en-IN"],
        "schema": {"type": "array", "items": {"type": "string"}},
    },
]


DEFAULTS: list[dict[str, Any]] = [
    *FLEET_DEFAULTS,
    *CHANNELS_DEFAULTS,
    *MEMORY_DEFAULTS,
    *INTEGRATIONS_DEFAULTS,
    *BRANDING_DEFAULTS,
]


__all__ = [
    "BRANDING_DEFAULTS",
    "CHANNELS_DEFAULTS",
    "DEFAULTS",
    "FLEET_DEFAULTS",
    "INTEGRATIONS_DEFAULTS",
    "MEMORY_DEFAULTS",
]
