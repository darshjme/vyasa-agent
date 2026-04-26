# SPDX-License-Identifier: Apache-2.0
"""Envato purchase-code verification (design-06 §2 row 10).

Live verification hits ``https://api.envato.com/v3/market/author/sale``
with a personal token. For v0.1 we read the token from the
``ENVATO_PERSONAL_TOKEN`` env var first (ops convenience), then fall back
to ``integrations.envato.personal_token`` in settings. If neither resolves
to a real token we return ``{ok: true}`` with ``mode: "stub"`` so local
development works, and emit a warning to the logger.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field


from ..deps import get_settings_store, require_admin


logger = logging.getLogger(__name__)

ENVATO_ENDPOINT = "https://api.envato.com/v3/market/author/sale"


router = APIRouter()


class VerifyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    license_code: str = Field(..., min_length=1, max_length=120)
    buyer_email: str | None = Field(default=None, max_length=320)
    product_code: str | None = Field(default=None, max_length=120)


def _resolve_token(store: Any) -> str | None:
    env_token = os.getenv("ENVATO_PERSONAL_TOKEN")
    if env_token and env_token.strip():
        return env_token.strip()
    settings_value = store.get("integrations.envato.personal_token")
    if isinstance(settings_value, str) and settings_value.strip():
        return settings_value.strip()
    return None


@router.post("/v1/license/verify")
async def verify_license(
    body: VerifyBody,
    _auth: dict[str, str] = Depends(require_admin),
    store: Any = Depends(get_settings_store),
) -> dict[str, Any]:
    token = _resolve_token(store)
    if token is None:
        logger.warning(
            "license verification running in stub mode: no envato token configured"
        )
        cache_ttl = int(store.get("integrations.envato.cache_ttl_s") or 21600)
        return {
            "ok": True,
            "valid": True,
            "mode": "stub",
            "tier": "unverified",
            "expires_at": None,
            "cached_for_s": cache_ttl,
        }

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                ENVATO_ENDPOINT,
                params={"code": body.license_code},
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError as exc:
        logger.warning("license verification network error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="license provider unavailable",
        ) from exc

    if resp.status_code == 404:
        return {
            "ok": False,
            "valid": False,
            "tier": None,
            "expires_at": None,
            "cached_for_s": 0,
        }
    if resp.status_code >= 500:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="license provider unavailable",
        )
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="license verification rejected",
        )

    data: dict[str, Any] = resp.json() if resp.content else {}
    item = data.get("item") or {}
    cache_ttl = int(store.get("integrations.envato.cache_ttl_s") or 21600)
    return {
        "ok": True,
        "valid": True,
        "mode": "live",
        "tier": item.get("name"),
        "expires_at": data.get("supported_until"),
        "buyer": data.get("buyer"),
        "purchase_count": data.get("purchase_count"),
        "cached_for_s": cache_ttl,
    }


__all__ = ["router"]
