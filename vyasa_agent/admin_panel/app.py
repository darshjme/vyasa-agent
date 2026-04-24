"""Admin panel FastAPI app factory (design-06 §2, design-08 §7).

:func:`create_app` wires the three runtime singletons (fleet manager, graph
store, settings store) into a FastAPI instance that mounts all ten v1 routes
on :port:`19000`. Two auth planes apply: gateway bearer for inbound message
paths, session cookie + double-submit CSRF for every admin path.

The app is deliberately thin — each router lives in its own module, and the
auth / error / settings primitives come from sibling modules so this file
stays under 180 lines and easy to audit.
"""

from __future__ import annotations

import logging
import os
import secrets
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .auth import GatewayBearer, SessionAuth
from .errors import (
    http_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from .routers import admin as admin_router
from .routers import employees as employees_router
from .routers import graph as graph_router
from .routers import license as license_router
from .routers import messages as messages_router
from .seeds import DEFAULTS
from .settings_store import SettingsStore

TRACE_HEADER = "X-Trace-ID"
SESSION_SECRET_ENV = "VYASA_ADMIN_SECRET"

logger = logging.getLogger(__name__)


def _resolve_secret() -> bytes:
    raw = os.getenv(SESSION_SECRET_ENV)
    if raw and raw.strip():
        return raw.strip().encode("utf-8")
    token = secrets.token_bytes(32)
    logger.warning(
        "session secret not set; generated ephemeral key. Set %s to persist sessions.",
        SESSION_SECRET_ENV,
    )
    return token


def _resolve_cors_origins(settings_store: SettingsStore) -> list[str]:
    raw = settings_store.get("admin.cors_origins")
    if isinstance(raw, list):
        return [str(o) for o in raw if isinstance(o, str) and o.strip()]
    return []


def _product_name(settings_store: SettingsStore) -> str:
    value = settings_store.get("branding.product_name")
    if isinstance(value, str) and value.strip():
        return value
    return "Admin Panel"


def create_app(
    fleet_manager: Any,
    graph_store: Any,
    settings_store: SettingsStore,
    *,
    seed: bool = True,
    secret_key: bytes | None = None,
) -> FastAPI:
    """Build the admin panel FastAPI instance and wire all routers.

    Parameters
    ----------
    fleet_manager:
        Any object implementing a subset of ``directory()``, ``is_alive(id)``,
        ``route_message(...)``, ``dispatch(...)``, ``handoff(...)``,
        ``set_enabled(...)``, ``status(id)``.
    graph_store:
        Any object implementing ``query(intent, k)`` and ``create_node(...)``.
    settings_store:
        :class:`SettingsStore` — the canonical settings table.
    seed:
        If ``True`` (default), :func:`SettingsStore.seed_defaults` is called
        with the design-08 defaults before the app starts serving traffic.
    secret_key:
        Optional override for the session-signing key. Production callers
        should persist one via :envvar:`VYASA_ADMIN_SECRET`.
    """
    if seed:
        settings_store.seed_defaults(DEFAULTS, actor="system")

    app = FastAPI(
        title=_product_name(settings_store),
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url="/v1/openapi.json",
    )

    app.state.fleet_manager = fleet_manager
    app.state.graph_store = graph_store
    app.state.settings_store = settings_store
    app.state.session_auth = SessionAuth(secret_key or _resolve_secret())
    app.state.gateway_bearer = GatewayBearer(settings=settings_store)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_resolve_cors_origins(settings_store),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-CSRF-Token", TRACE_HEADER],
    )

    @app.middleware("http")
    async def _trace_id_middleware(request: Request, call_next):
        incoming = request.headers.get(TRACE_HEADER)
        trace_id = incoming if incoming else uuid.uuid4().hex
        request.state.trace_id = trace_id
        response = await call_next(request)
        response.headers[TRACE_HEADER] = trace_id
        response.headers["X-Request-ID"] = trace_id
        return response

    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    app.include_router(messages_router.router)
    app.include_router(employees_router.router)
    app.include_router(graph_router.router)
    app.include_router(admin_router.router)
    app.include_router(license_router.router)

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        ready = True
        checks: dict[str, bool] = {}
        for name, obj in (
            ("fleet_manager", fleet_manager),
            ("graph_store", graph_store),
            ("settings_store", settings_store),
        ):
            ok = obj is not None
            checks[name] = ok
            ready = ready and ok
        status_code = 200 if ready else 503
        return JSONResponse({"ready": ready, "checks": checks}, status_code=status_code)

    return app


__all__ = ["create_app"]
