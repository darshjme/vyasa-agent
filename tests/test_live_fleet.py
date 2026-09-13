"""Exercise actual fleet actors, API, persistent runtime and tool dispatch."""

from types import SimpleNamespace

import httpx
import pytest

from vyasa_agent.admin_panel.app import create_app
from vyasa_agent.admin_panel.settings_store import SettingsStore
from vyasa_agent.fleet.descriptor import load_fleet
from vyasa_agent.fleet.manager import FleetManager
from vyasa_agent.fleet.registry_resolver import resolve_prompt
from vyasa_agent.gateway.service import GatewayService
from vyasa_agent.graphify.store import GraphStore
from vyasa_agent.paths import fleet_root


@pytest.fixture
async def running(tmp_path, monkeypatch):
    monkeypatch.setenv("VYASA_HOME", str(tmp_path))
    monkeypatch.setenv("VYASA_API_KEY", "test-key")
    monkeypatch.setenv("VYASA_MODEL", "fixture-model")
    monkeypatch.delenv("VYASA_STUB_BRIDGE", raising=False)
    from vyasa_agent import runtime

    requests = []

    class Reply:
        tool_calls = None
        content = "verified reply"

    class Client:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def create(self, **kwargs):
            requests.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=Reply())])

    monkeypatch.setattr(runtime, "AsyncOpenAI", Client)
    settings = SettingsStore(tmp_path / "settings.sqlite")
    settings.set(
        "channels.gateway.tokens",
        [{"token": "vya_live_test"}, {"token": "vya_live_other"}],
        user="test",
        section="channels",
    )
    graph = GraphStore(tmp_path / "graph.sqlite")
    fleet = FleetManager()
    await fleet.boot(fleet_root(), settings_store=settings, graph_client=graph)
    app = create_app(GatewayService(fleet), graph, settings, secret_key=b"test-secret")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer vya_live_test"},
    ) as client:
        yield client, requests, fleet, app
    await fleet.shutdown()
    await graph.close()
    settings.close()


def test_every_bundled_prompt_resolves(monkeypatch):
    monkeypatch.delenv("GRAYMATTER_HOME", raising=False)
    descriptors = load_fleet(fleet_root())[1]
    assert len(descriptors) == 29
    for descriptor in descriptors:
        assert len(resolve_prompt(descriptor.system_prompt_ref)) > 100


async def test_authenticated_chat_history_and_token_isolation(running):
    client, requests, _, _ = running
    assert (
        await client.get("/v1/fleet", headers={"Authorization": "Bearer wrong"})
    ).status_code == 401
    assert len((await client.get("/v1/fleet")).json()["employees"]) == 29
    for text in ["remember blue", "what color?"]:
        reply = await client.post(
            "/v1/chat", json={"employee": "sarabhai", "session": "one", "text": text}
        )
        assert reply.status_code == 200, reply.text
        assert reply.json()["employee_id"] == "dr.sarabhai"
        assert "verified reply" in reply.json()["text"]
    assert len(requests[-1]["messages"]) == 4
    await client.post(
        "/v1/chat",
        json={"employee": "sarabhai", "session": "one", "text": "new owner"},
        headers={"Authorization": "Bearer vya_live_other"},
    )
    assert len(requests[-1]["messages"]) == 2
    await client.post(
        "/v1/chat", json={"employee": "sarabhai", "session": "two", "text": "new chat"}
    )
    assert len(requests[-1]["messages"]) == 2


async def test_real_legacy_dispatch_and_disabled_employee(running):
    client, _, fleet, _ = running
    result = await client.post(
        "/v1/dispatch/prometheus", json={"intent": "debug", "payload": {"text": "check"}}
    )
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "completed"
    fleet.set_enabled("prometheus", False)
    result = await client.post("/v1/chat", json={"employee": "prometheus", "text": "check"})
    assert result.status_code == 404
    assert (
        await client.post("/v1/chat", json={"employee": "missing", "text": "check"})
    ).status_code == 404


async def test_real_graph_api_and_forged_csrf(running):
    client, _, _, app = running
    from vyasa_agent.admin_panel.auth import CSRF_COOKIE, SESSION_COOKIE

    cookie, csrf = app.state.session_auth.issue_session("tester")
    client.cookies.set(SESSION_COOKIE, cookie)
    client.cookies.set(CSRF_COOKIE, csrf)
    body = {"intent": "integration", "summary": "known note", "author_employee_id": "vyasa"}
    reply = await client.post("/v1/graph/nodes", json=body, headers={"X-CSRF-Token": csrf})
    assert reply.status_code == 201, reply.text
    assert (await client.get("/v1/graph/query?intent=integration")).json()["nodes"][0][
        "summary"
    ] == "known note"
    client.cookies.set(CSRF_COOKIE, "forged")
    reply = await client.post("/v1/graph/nodes", json=body, headers={"X-CSRF-Token": "forged"})
    assert reply.status_code == 403


async def test_channel_reply_reaches_adapter(running):
    _, _, _, app = running
    from vyasa_agent.gateway.types import InboundMessage

    replies = []

    class Adapter:
        async def send(self, reply):
            replies.append(reply)

    await app.state.fleet_manager.handler(Adapter())(
        InboundMessage(
            platform="console",
            platform_user_id="local",
            platform_chat_id="local",
            text="/ask prometheus debug",
            trace_id="trace",
        )
    )
    assert len(replies) == 1
    assert "verified reply" in replies[0].text


async def test_memory_tool_scoping_and_persistence(tmp_path, monkeypatch):
    from vyasa_agent.runtime import AIAgent

    monkeypatch.setenv("VYASA_API_KEY", "test")
    graph = GraphStore(tmp_path / "graph.sqlite")
    agent = AIAgent(
        ephemeral_system_prompt="test",
        session_db=str(tmp_path / "state.db"),
        model="test",
        provider="openrouter",
        employee_id="prometheus",
        graph_client=graph,
    )
    await agent._memory_tool("graph_write", {"summary": "blue choice"}, "session-a")
    assert len(await agent._memory_tool("graph_read", {"query": "blue"}, "session-a")) == 1
    assert await agent._memory_tool("graph_read", {"query": "blue"}, "session-b") == []
    await graph.close()
