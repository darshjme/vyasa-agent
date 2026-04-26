# SPDX-License-Identifier: Apache-2.0
"""Admin settings endpoints (design-06 §2 rows 8–9, design-08 §7).

``GET /v1/admin/settings``  — returns all rows grouped by section. Optional
``?section=branding`` filter for the Settings sub-page.
``POST /v1/admin/settings`` — upsert ``{key, value, section?, schema?}``.
Writes are CSRF-guarded and always log one audit entry (the settings store
does that inside the same transaction).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from ..deps import get_fleet_manager, get_settings_store, require_admin


router = APIRouter()


class UpsertBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    key: str = Field(..., min_length=1, max_length=200)
    value: Any
    section: str | None = Field(default=None, max_length=60)
    field_schema: dict[str, Any] | None = Field(default=None, alias="schema")
    reason: str | None = Field(default=None, max_length=280)


@router.get("/v1/admin/settings")
async def list_settings(
    section: str | None = Query(default=None, max_length=60),
    _auth: dict[str, str] = Depends(require_admin),
    store: Any = Depends(get_settings_store),
) -> dict[str, Any]:
    if section:
        rows = store.list(section=section)
        return {"section": section, "settings": rows}
    grouped = store.list_grouped()
    return {
        "sections": sorted(grouped.keys()),
        "settings_by_section": grouped,
    }


@router.post("/v1/admin/settings")
async def upsert_setting(
    body: UpsertBody,
    auth: dict[str, str] = Depends(require_admin),
    store: Any = Depends(get_settings_store),
    fleet: Any = Depends(get_fleet_manager),
) -> dict[str, Any]:
    actor = auth.get("subject", "admin")
    stored = store.set(
        body.key,
        body.value,
        actor,
        section=body.section,
        schema=body.field_schema,
    )

    # Fan the change out to every registered overlay subscriber (fleet,
    # channel adapters, branding) so the new value takes effect without an
    # app restart.  The overlay swallows subscriber exceptions.
    overlay = getattr(fleet, "overlay", None)
    if overlay is not None:
        notify = getattr(overlay, "notify_change", None)
        if callable(notify):
            notify(body.key, body.value)

    return {
        "key": stored["key"],
        "section": stored["section"],
        "updated_at": stored["updated_at"],
        "updated_by": stored["updated_by"],
    }


__all__ = ["router"]
