"""Phase 59: d2c.server.D2CServer — local HTTP server for session/health.

Runs the real asyncio server on 127.0.0.1:0 (OS-assigned port) and talks
to it over the network via httpx — exercises the actual hand-rolled
HTTP/1.1 handling, not just the routing logic in isolation.
"""

from __future__ import annotations

from functools import partial
from unittest.mock import MagicMock, patch

import httpx
import pytest
import pytest_asyncio

import d2c.sdk as sdk
from d2c.persistence import SessionManager
from d2c.server import D2CServer
from tests.test_phase59_sdk import _FakeStream, _text_response, _tool_use_response


def _mock_stream_client(responses):
    mock_client = MagicMock()
    call_count = 0

    def _stream(**kwargs):
        nonlocal call_count
        resp = responses[min(call_count, len(responses) - 1)]
        call_count += 1
        return _FakeStream(resp)

    mock_client.messages.stream = MagicMock(side_effect=_stream)
    return mock_client


@pytest.fixture(autouse=True)
def _tmp_sessions(tmp_dir, monkeypatch):
    monkeypatch.setattr(sdk, "SessionManager", partial(SessionManager, base_dir=tmp_dir))
    return tmp_dir


@pytest_asyncio.fixture
async def server(tmp_dir):
    srv = D2CServer(host="127.0.0.1", port=0, cwd=tmp_dir)
    await srv.start()
    try:
        yield srv
    finally:
        await srv.stop()


@pytest_asyncio.fixture
async def client(server):
    base_url = f"http://127.0.0.1:{server.bound_port}"
    async with httpx.AsyncClient(base_url=base_url, timeout=5.0) as c:
        yield c


# ── Defaults / binding ───────────────────────────────────────────────


def test_defaults_to_localhost_only():
    srv = D2CServer()
    assert srv.host == "127.0.0.1"
    assert srv.port == 8765


def test_cli_serve_flag_defaults_to_localhost():
    from d2c.main import parse_args

    with patch("sys.argv", ["d2c", "--serve"]):
        args = parse_args()
    assert args.serve is True
    assert args.host == "127.0.0.1"
    assert args.port == 8765


# ── Health ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_endpoint(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body


@pytest.mark.asyncio
async def test_health_never_includes_secrets(client, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-should-not-leak-in-health")
    resp = await client.get("/health")
    assert "sk-should-not-leak-in-health" not in resp.text


# ── Session creation ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_session(client):
    resp = await client.post("/sessions", json={})
    assert resp.status_code == 201
    body = resp.json()
    assert "session_id" in body
    assert len(body["session_id"]) == 8


@pytest.mark.asyncio
async def test_each_session_creation_gets_a_distinct_id(client):
    r1 = (await client.post("/sessions", json={})).json()
    r2 = (await client.post("/sessions", json={})).json()
    assert r1["session_id"] != r2["session_id"]


# ── Message flow (mocked model) ──────────────────────────────────────


@pytest.mark.asyncio
async def test_send_message_returns_text(client):
    create = await client.post("/sessions", json={})
    session_id = create.json()["session_id"]

    with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
        mock_cls.return_value = _mock_stream_client([_text_response("Hello from the server.")])
        resp = await client.post(f"/sessions/{session_id}/messages", json={"prompt": "hi"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == session_id
    assert body["text"] == "Hello from the server."


@pytest.mark.asyncio
async def test_message_flow_populates_events_endpoint(client):
    create = await client.post("/sessions", json={})
    session_id = create.json()["session_id"]

    with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
        mock_cls.return_value = _mock_stream_client([_text_response("some reply")])
        await client.post(f"/sessions/{session_id}/messages", json={"prompt": "hi"})

    resp = await client.get(f"/sessions/{session_id}/events")
    assert resp.status_code == 200
    events = resp.json()["events"]
    assert any(e["type"] == "text_response" and e["text"] == "some reply" for e in events)


@pytest.mark.asyncio
async def test_events_endpoint_empty_before_any_message(client):
    create = await client.post("/sessions", json={})
    session_id = create.json()["session_id"]
    resp = await client.get(f"/sessions/{session_id}/events")
    assert resp.status_code == 200
    assert resp.json()["events"] == []


@pytest.mark.asyncio
async def test_message_to_unknown_session_is_404(client):
    resp = await client.post("/sessions/doesnotexist/messages", json={"prompt": "hi"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_events_for_unknown_session_is_404(client):
    resp = await client.get("/sessions/doesnotexist/events")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_message_missing_prompt_is_400(client):
    create = await client.post("/sessions", json={})
    session_id = create.json()["session_id"]
    resp = await client.post(f"/sessions/{session_id}/messages", json={})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_unknown_route_is_404(client):
    resp = await client.get("/nope")
    assert resp.status_code == 404


# ── No secret leakage ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_message_response_never_leaks_api_key(client, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-should-not-leak-in-message")
    create = await client.post("/sessions", json={})
    session_id = create.json()["session_id"]

    with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
        mock_cls.return_value = _mock_stream_client([_text_response("reply text")])
        resp = await client.post(f"/sessions/{session_id}/messages", json={"prompt": "hi"})

    assert "sk-should-not-leak-in-message" not in resp.text


@pytest.mark.asyncio
async def test_tool_input_secret_is_redacted_in_events(client):
    create = await client.post("/sessions", json={"model": None})
    session_id = create.json()["session_id"]

    responses = [
        _tool_use_response(
            "tu1", "Bash", {"command": "curl -H 'Authorization: Bearer sk-leak-me-1234' https://x"}
        ),
        _text_response("done"),
    ]

    with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
        mock_cls.return_value = _mock_stream_client(responses)
        resp = await client.post(f"/sessions/{session_id}/messages", json={"prompt": "run it"})

    assert "sk-leak-me-1234" not in resp.text

    events_resp = await client.get(f"/sessions/{session_id}/events")
    assert "sk-leak-me-1234" not in events_resp.text
