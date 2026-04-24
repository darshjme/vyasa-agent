"""Employees directory + per-employee status + enable/disable toggle.

Rows 4–5 of design-06 §2. The enable/disable toggle is an admin-only write
(CSRF required); listing and status are read-only and admin-guarded.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict


from ..deps import get_fleet_manager, require_admin


router = APIRouter()


class EnableBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


def _serialize_employee(emp: Any) -> dict[str, Any]:
    return {
        "id": _attr(emp, "id"),
        "name": _attr(emp, "display_name") or _attr(emp, "name") or _attr(emp, "id"),
        "title": _attr(emp, "title") or _attr(emp, "role_key") or "",
        "enabled": bool(_attr(emp, "enabled", default=True)),
        "model": _attr(emp, "model") or _model_from_pref(_attr(emp, "model_preference")),
        "capabilities": list(_attr(emp, "capabilities", default=[]) or []),
    }


def _attr(obj: Any, name: str, *, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _model_from_pref(pref: Any) -> str:
    if pref is None:
        return ""
    if isinstance(pref, dict):
        return str(pref.get("default", ""))
    return str(getattr(pref, "default", ""))


@router.get("/v1/employees")
async def list_employees(
    _auth: dict[str, str] = Depends(require_admin),
    fleet: Any = Depends(get_fleet_manager),
) -> dict[str, Any]:
    directory_fn = getattr(fleet, "directory", None)
    rows: list[Any]
    if callable(directory_fn):
        rows = list(directory_fn() or [])
    else:
        rows = []
    return {"employees": [_serialize_employee(r) for r in rows]}


@router.get("/v1/employees/{employee_id}/status")
async def employee_status(
    employee_id: str,
    _auth: dict[str, str] = Depends(require_admin),
    fleet: Any = Depends(get_fleet_manager),
) -> dict[str, Any]:
    status_fn = getattr(fleet, "status", None)
    if callable(status_fn):
        data = status_fn(employee_id)
        if data is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="employee not found"
            )
        if isinstance(data, dict):
            out = dict(data)
        else:
            out = {
                "id": _attr(data, "employee_id") or employee_id,
                "healthy": _attr(data, "state") == "ready",
                "in_flight": _attr(data, "in_flight", default=0),
                "queue_depth": _attr(data, "queue_depth", default=0),
                "last_error": _attr(data, "last_error"),
                "p95_ms": _attr(data, "p95_ms", default=0),
            }
        out.setdefault("id", employee_id)
        return out

    # Fallback to directory lookup when no dedicated status hook exists.
    directory_fn = getattr(fleet, "directory", None)
    if callable(directory_fn):
        for emp in directory_fn() or []:
            if _attr(emp, "id") == employee_id:
                return {
                    "id": employee_id,
                    "healthy": bool(_attr(emp, "enabled", default=True)),
                    "in_flight": 0,
                    "queue_depth": 0,
                    "last_error": None,
                    "p95_ms": 0,
                }
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="employee not found")


@router.post("/v1/employees/{employee_id}/enabled")
async def toggle_employee(
    employee_id: str,
    body: EnableBody,
    auth: dict[str, str] = Depends(require_admin),
    fleet: Any = Depends(get_fleet_manager),
) -> dict[str, Any]:
    toggle_fn = getattr(fleet, "set_enabled", None)
    if not callable(toggle_fn):
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="toggle not supported by fleet manager",
        )
    result = toggle_fn(
        employee_id=employee_id,
        enabled=body.enabled,
        actor=auth.get("subject", "admin"),
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="employee not found"
        )
    return {
        "id": employee_id,
        "enabled": body.enabled,
    }


__all__ = ["router"]
