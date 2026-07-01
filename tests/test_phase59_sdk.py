"""Phase 59: d2c.sdk.D2CClient — programmatic API around queryLoop().

D2CClient always runs with stream=True (matches CLI headless), so the
model client is mocked at ``.messages.stream()`` — an async context
manager yielding an empty live-event stream and a ``get_final_message()``
result — rather than ``.messages.create()``.
"""

from __future__ import annotations

from functools import partial
from unittest.mock import MagicMock, patch

import pytest

import d2c.sdk as sdk
from d2c.loop import TextResponse, ToolExecutionEvent
from d2c.persistence import SessionManager

# ── Fake streaming model client ─────────────────────────────────────


class _FakeStream:
    """Minimal stand-in for anthropic's MessageStreamManager: an empty live
    event stream (loop.py only needs get_final_message() for content)."""

    def __init__(self, final_message):
        self._final_message = final_message

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    def __aiter__(self):
        async def _empty():
            return
            yield  # pragma: no cover — makes this an async generator

        return _empty()

    async def get_final_message(self):
        return self._final_message


def _text_response(text: str):
    response = MagicMock(stop_reason="end_turn")
    response.content = [MagicMock(type="text", text=text)]
    return response


def _tool_use_response(tool_id: str, name: str, tool_input: dict):
    # MagicMock(name=...) sets the mock's own debug repr, not a `.name`
    # attribute — set it post-construction to get a real string value.
    response = MagicMock(stop_reason="tool_use")
    block = MagicMock(type="tool_use", id=tool_id, input=tool_input)
    block.name = name
    response.content = [block]
    return response


def _mock_stream_client(responses):
    """A MagicMock anthropic client whose .messages.stream(...) yields each
    response in order (last one repeats), matching test_e2e.py's
    side_effect convention for .messages.create."""
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
    """Point D2CClient's SessionManager at a temp dir, not the real ~/.d2c."""
    monkeypatch.setattr(sdk, "SessionManager", partial(SessionManager, base_dir=tmp_dir))
    return tmp_dir


# ── run() wrapper ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_yields_text_response(tmp_dir):
    client = sdk.D2CClient(cwd=tmp_dir)

    with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
        mock_cls.return_value = _mock_stream_client([_text_response("Hello from the SDK.")])
        events = [e async for e in client.run("hi")]

    text_events = [e for e in events if isinstance(e, TextResponse)]
    assert len(text_events) == 1
    assert text_events[0].text == "Hello from the SDK."


@pytest.mark.asyncio
async def test_run_creates_a_session_and_populates_session_id(tmp_dir):
    client = sdk.D2CClient(cwd=tmp_dir)
    assert client.session_id is None

    with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
        mock_cls.return_value = _mock_stream_client([_text_response("ok")])
        async for _ in client.run("hi"):
            pass

    assert client.session_id is not None
    assert len(client.session_id) == 8  # d2c session ids are 8-char


@pytest.mark.asyncio
async def test_run_resumes_an_existing_session(tmp_dir):
    manager = SessionManager(base_dir=tmp_dir)
    existing = manager.create_session(tmp_dir)

    client = sdk.D2CClient(cwd=tmp_dir, session_id=existing.session_id)

    with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
        mock_cls.return_value = _mock_stream_client([_text_response("resumed")])
        async for _ in client.run("continue"):
            pass

    assert client.session_id == existing.session_id


@pytest.mark.asyncio
async def test_second_run_reuses_the_first_run_session(tmp_dir):
    client = sdk.D2CClient(cwd=tmp_dir)

    with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
        mock_cls.return_value = _mock_stream_client([_text_response("ok")])

        async for _ in client.run("first"):
            pass
        first_id = client.session_id
        async for _ in client.run("second"):
            pass

    assert client.session_id == first_id


@pytest.mark.asyncio
async def test_run_forwards_tool_execution_events(tmp_dir):
    f = tmp_dir / "notes.txt"
    f.write_text("secret plan")
    # Like headless CLI, D2CClient has no interactive approval_callback, so
    # ASK decisions fail closed under the "default" mode — use "bypass" so
    # this test exercises tool dispatch rather than the permission gate.
    client = sdk.D2CClient(cwd=tmp_dir, permission_mode="bypass")

    responses = [
        _tool_use_response("tu1", "Read", {"file_path": str(f)}),
        _text_response("Found: secret plan"),
    ]

    with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
        mock_cls.return_value = _mock_stream_client(responses)
        events = [e async for e in client.run("read notes.txt")]

    tool_events = [e for e in events if isinstance(e, ToolExecutionEvent)]
    assert len(tool_events) == 1
    assert tool_events[0].tool_use.name == "Read"
    assert "secret plan" in tool_events[0].result.output


@pytest.mark.asyncio
async def test_run_never_raises_on_no_api_key(tmp_dir, monkeypatch):
    """A missing DEEPSEEK_API_KEY should surface as a clean TextResponse
    error, not an unhandled exception — same contract as CLI headless."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    client = sdk.D2CClient(cwd=tmp_dir)
    events = [e async for e in client.run("hi")]
    text_events = [e for e in events if isinstance(e, TextResponse)]
    assert text_events and "DEEPSEEK_API_KEY" in text_events[0].text
