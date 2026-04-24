"""FastAPI dependency helpers shared by every router.

Routers call :func:`get_fleet_manager`, :func:`get_graph_store`, and
:func:`get_settings_store` instead of reaching into ``app.state`` directly;
that indirection lets tests override a single function instead of
monkey-patching the whole app.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, Request

from .auth import GatewayBearer, SessionAuth


def get_fleet_manager(request: Request) -> Any:
    return request.app.state.fleet_manager


def get_graph_store(request: Request) -> Any:
    return request.app.state.graph_store


def get_settings_store(request: Request) -> Any:
    return request.app.state.settings_store


def get_session_auth(request: Request) -> SessionAuth:
    return request.app.state.session_auth


def get_gateway_bearer(request: Request) -> GatewayBearer:
    return request.app.state.gateway_bearer


def require_gateway(
    request: Request,
    bearer: GatewayBearer = Depends(get_gateway_bearer),
) -> dict[str, str]:
    return bearer.verify(request)


def require_admin(
    request: Request,
    session: SessionAuth = Depends(get_session_auth),
) -> dict[str, str]:
    method = request.method.upper()
    require_csrf = method not in {"GET", "HEAD", "OPTIONS"}
    return session.verify(request, require_csrf=require_csrf)


__all__ = [
    "get_fleet_manager",
    "get_gateway_bearer",
    "get_graph_store",
    "get_session_auth",
    "get_settings_store",
    "require_admin",
    "require_gateway",
]
