"""Smoke tests for the admin panel FastAPI app.

Boots the app with in-memory stores and exercises the auth + happy-path
surface: health checks, empty directory, CSRF enforcement, bearer
validation, settings upsert round-trip, license stub.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from vyasa_agent.admin_panel.app import create_app
from vyasa_agent.admin_panel.auth import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE
from vyasa_agent.admin_panel.settings_store import SettingsStore


class _FakeFleet:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self._roster: list[dict[str, Any]] = []

    def directory(self) -> list[dict[str, Any]]:
        return list(self._roster)

    def is_alive(self, employee_id: str) -> bool:
        return any(r["id"] == employee_id and r.get("enabled", True) for r in self._roster)

    async def route_message(self, payload: dict[str, Any], *, trace_id: str) -> str:
        self.calls.append(trace_id)
        return self._roster[0]["id"] if self._roster else "unrouted"


class _FakeGraph:
    def __init__(self) -> None:
        self.nodes: list[dict[str, Any]] = []

    def query(self, *, intent: str, k: int) -> list[dict[str, Any]]:
        return [n for n in self.nodes if n["intent"] == intent][:k]

    def create_node(self, **kwargs: Any) -> dict[str, Any]:
        node = {"id": f"n_{len(self.nodes) + 1:04d}", "version": 1, **kwargs}
        self.nodes.append(node)
        return node


@pytest.fixture
def settings_store() -> SettingsStore:
    store = SettingsStore(":memory:")
    yield store
    store.close()


@pytest.fixture
def app(settings_store: SettingsStore):
    fleet = _FakeFleet()
    graph = _FakeGraph()
    instance = create_app(
        fleet_manager=fleet,
        graph_store=graph,
        settings_store=settings_store,
        secret_key=b"test-secret-key-for-admin-panel-tests",
    )
    instance.state.test_fleet = fleet
    instance.state.test_graph = graph
    return instance


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


def _admin_cookies(app) -> tuple[dict[str, str], str]:
    session_cookie, csrf_token = app.state.session_auth.issue_session("test-admin")
    return (
        {SESSION_COOKIE: session_cookie, CSRF_COOKIE: csrf_token},
        csrf_token,
    )


async def test_healthz_and_readyz(client: httpx.AsyncClient) -> None:
    health = await client.get("/healthz")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    ready = await client.get("/readyz")
    assert ready.status_code == 200
    body = ready.json()
    assert body["ready"] is True
    assert body["checks"]["fleet_manager"] is True


async def test_list_employees_empty_requires_session(app, client: httpx.AsyncClient) -> None:
    unauth = await client.get("/v1/employees")
    assert unauth.status_code == 401
    assert unauth.headers["content-type"].startswith("application/problem+json")

    cookies, _ = _admin_cookies(app)
    authed = await client.get("/v1/employees", cookies=cookies)
    assert authed.status_code == 200
    assert authed.json() == {"employees": []}


async def test_admin_settings_upsert_requires_csrf(app, client: httpx.AsyncClient) -> None:
    cookies, csrf = _admin_cookies(app)

    # Session cookie alone is not enough for a write.
    no_csrf = await client.post(
        "/v1/admin/settings",
        json={"key": "branding.product_name", "value": "Renamed"},
        cookies=cookies,
    )
    assert no_csrf.status_code == 403
    assert no_csrf.headers["content-type"].startswith("application/problem+json")

    # Matching CSRF header passes.
    ok = await client.post(
        "/v1/admin/settings",
        json={"key": "branding.product_name", "value": "Renamed", "section": "branding"},
        cookies=cookies,
        headers={CSRF_HEADER: csrf},
    )
    assert ok.status_code == 200
    assert ok.json()["key"] == "branding.product_name"

    listed = await client.get("/v1/admin/settings?section=branding", cookies=cookies)
    assert listed.status_code == 200
    names = {row["key"]: row["value"] for row in listed.json()["settings"]}
    assert names["branding.product_name"] == "Renamed"


async def test_inbound_message_rejects_invalid_bearer(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/v1/messages",
        json={
            "adapter": "telegram",
            "adapter_msg_id": "1",
            "sender": "+91999",
            "channel": "direct",
            "text": "hi",
        },
        headers={"Authorization": "Bearer wrong_token"},
    )
    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/problem+json")


async def test_inbound_message_accepts_configured_bearer(
    app, client: httpx.AsyncClient, settings_store: SettingsStore
) -> None:
    token = "vya_live_testtoken_abcdef"
    settings_store.set(
        "channels.gateway.tokens",
        [{"token": token, "scope": "adapter:telegram", "label": "primary"}],
        "system",
        section="channels",
    )
    resp = await client.post(
        "/v1/messages",
        json={
            "adapter": "telegram",
            "adapter_msg_id": "42",
            "sender": "+91999",
            "channel": "direct",
            "text": "hello",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["message_id"].startswith("msg_")
    assert "trace_id" in body


async def test_license_verify_stub_mode(app, client: httpx.AsyncClient) -> None:
    cookies, csrf = _admin_cookies(app)
    resp = await client.post(
        "/v1/license/verify",
        json={"license_code": "unused-when-stub"},
        cookies=cookies,
        headers={CSRF_HEADER: csrf},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["mode"] == "stub"


async def test_defaults_seeded_on_boot(
    app, client: httpx.AsyncClient, settings_store: SettingsStore
) -> None:
    cookies, _ = _admin_cookies(app)
    resp = await client.get("/v1/admin/settings?section=branding", cookies=cookies)
    assert resp.status_code == 200
    keys = {row["key"] for row in resp.json()["settings"]}
    assert "branding.product_name" in keys
    assert "branding.primary_color" in keys
    assert settings_store.get("branding.primary_color") == "#0F2E3D"
    assert settings_store.get("branding.accent_color") == "#E28822"
    assert settings_store.get("branding.ivory_color") == "#F6F1E5"
    assert settings_store.get("branding.locale.default") == "en-IN"
