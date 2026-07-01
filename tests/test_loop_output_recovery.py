"""Phase 35: bounded output-token recovery in queryLoop().

When the model stops at the output-token cap (stop_reason == "max_tokens"),
the loop retries the same turn with a larger max_tokens budget, up to
MAX_OUTPUT_TOKENS_RECOVERY attempts, then lets the latest partial text
through. These tests mock the model so no network access is required.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from d2c.config import Config
from d2c.loop import (
    BASE_MAX_TOKENS,
    MAX_MAX_TOKENS,
    MAX_OUTPUT_TOKENS_RECOVERY,
    LoopConfig,
    StubHookRegistry,
    StubPermissionEngine,
    TextResponse,
    ToolExecutionEvent,
    queryLoop,
)
from d2c.tools import PermissionCategory, Tool, ToolResult

# ── Mocks ─────────────────────────────────────────────────────────────


class MockContentBlock:
    def __init__(self, block_type: str, text: str = "", **kwargs):
        self.type = block_type
        self.text = text
        for k, v in kwargs.items():
            setattr(self, k, v)


@dataclass
class MockResponse:
    content: list
    stop_reason: str = "end_turn"


def text_block(text: str, stop_reason: str = "end_turn") -> MockResponse:
    return MockResponse(content=[MockContentBlock("text", text=text)], stop_reason=stop_reason)


def tool_block(stop_reason: str = "end_turn") -> MockResponse:
    block = MockContentBlock("tool_use", id="tu_1", name="Read", input={"file_path": "/x.txt"})
    return MockResponse(content=[block], stop_reason=stop_reason)


class MockReadTool(Tool):
    name = "Read"
    description = "Read a file"
    input_schema = {
        "type": "object",
        "properties": {"file_path": {"type": "string"}},
        "required": ["file_path"],
    }
    category = PermissionCategory.READ
    is_concurrent_safe = True

    async def execute(self, file_path: str, **kwargs) -> ToolResult:
        return ToolResult(output=f"content of {file_path}")


def make_config(max_turns: int = 25, stream: bool = False) -> LoopConfig:
    config = Config(model="deepseek-v4-pro", max_turns=max_turns)
    return LoopConfig(
        system_prompt="You are a helpful agent.",
        user_context="Today's date: 2026-07-01",
        model="deepseek-v4-pro",
        max_turns=max_turns,
        tools=[MockReadTool()],
        permission_engine=StubPermissionEngine(),
        hooks=StubHookRegistry(),
        config=config,
        deepseek_api_key="test-key",
        stream=stream,
    )


class MockStream:
    """Minimal async-context-manager + async-iterator matching client.messages.stream."""

    def __init__(self, final_msg: MockResponse):
        self._final = final_msg

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def __aiter__(self):
        return self._empty()

    async def _empty(self):
        if False:  # pragma: no cover - empty async generator
            yield None

    async def get_final_message(self):
        return self._final


async def _drive(config: LoopConfig, create_side_effect, budgets: list):
    """Run queryLoop with a mocked non-streaming client, recording max_tokens."""

    async def side_effect(**kwargs):
        budgets.append(kwargs["max_tokens"])
        return create_side_effect()

    with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(side_effect=side_effect)
        mock_cls.return_value = mock_client
        events = []
        async for event in queryLoop(config, [{"role": "user", "content": "go"}]):
            events.append(event)
    return events


# ── Tests ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_first_truncation_retries_with_doubled_budget():
    """Item 1: a max_tokens stop retries the same turn with a doubled budget."""
    budgets: list[int] = []
    calls = [0]

    def make():
        calls[0] += 1
        if calls[0] == 1:
            return text_block("partial", stop_reason="max_tokens")
        return text_block("complete answer", stop_reason="end_turn")

    events = await _drive(make_config(), make, budgets)

    assert budgets == [BASE_MAX_TOKENS, BASE_MAX_TOKENS * 2]  # 8192, 16384
    assert isinstance(events[-1], TextResponse)
    assert "complete" in events[-1].text


@pytest.mark.asyncio
async def test_retries_capped_then_returns_latest_text():
    """Item 2: recovery stops after MAX_OUTPUT_TOKENS_RECOVERY and returns latest text."""
    budgets: list[int] = []
    calls = [0]

    def make():
        calls[0] += 1
        return text_block(f"partial-{calls[0]}", stop_reason="max_tokens")

    events = await _drive(make_config(), make, budgets)

    # 1 initial + 3 retries = 4 model calls; budget escalates then caps at 32768.
    assert budgets == [8192, 16384, 32768, MAX_MAX_TOKENS]
    assert calls[0] == MAX_OUTPUT_TOKENS_RECOVERY + 1
    assert isinstance(events[-1], TextResponse)
    assert events[-1].text == "partial-4"  # latest partial flows through


@pytest.mark.asyncio
async def test_counter_resets_after_non_truncated_turn():
    """Item 3: a non-truncated (tool) turn resets the counter, so budget returns to base."""
    budgets: list[int] = []
    calls = [0]

    def make():
        calls[0] += 1
        if calls[0] == 1:
            return text_block("partial", stop_reason="max_tokens")  # retry → attempts=1
        if calls[0] == 2:
            return tool_block(stop_reason="end_turn")  # tool turn → reset
        if calls[0] == 3:
            return text_block("partial2", stop_reason="max_tokens")  # retry again from base
        return text_block("done", stop_reason="end_turn")

    events = await _drive(make_config(), make, budgets)

    # Budget for the 3rd call is back to base (8192), proving the reset.
    assert budgets == [8192, 16384, 8192, 16384]
    assert any(isinstance(e, ToolExecutionEvent) for e in events)
    assert isinstance(events[-1], TextResponse)
    assert events[-1].text == "done"


@pytest.mark.asyncio
async def test_tool_calls_are_not_retried():
    """Item 4: a max_tokens response with tool calls does not retry; it dispatches."""
    budgets: list[int] = []
    calls = [0]

    def make():
        calls[0] += 1
        if calls[0] == 1:
            return tool_block(stop_reason="max_tokens")  # truncated BUT has a tool call
        return text_block("after tool", stop_reason="end_turn")

    events = await _drive(make_config(), make, budgets)

    # No recovery retry inserted: exactly 2 calls, both at base budget.
    assert budgets == [BASE_MAX_TOKENS, BASE_MAX_TOKENS]
    assert calls[0] == 2
    assert any(isinstance(e, ToolExecutionEvent) for e in events)


@pytest.mark.asyncio
async def test_streaming_passes_escalated_max_tokens():
    """Item 5: streaming mode passes the escalated max_tokens into messages.stream."""
    budgets: list[int] = []
    calls = [0]

    def make_stream(**kwargs):
        budgets.append(kwargs["max_tokens"])
        calls[0] += 1
        if calls[0] == 1:
            return MockStream(text_block("partial", stop_reason="max_tokens"))
        return MockStream(text_block("streamed done", stop_reason="end_turn"))

    with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
        mock_client = MagicMock()
        mock_client.messages.stream = MagicMock(side_effect=make_stream)
        mock_cls.return_value = mock_client
        events = []
        async for event in queryLoop(make_config(stream=True), [{"role": "user", "content": "go"}]):
            events.append(event)

    assert budgets == [BASE_MAX_TOKENS, BASE_MAX_TOKENS * 2]  # 8192, 16384
    assert isinstance(events[-1], TextResponse)
