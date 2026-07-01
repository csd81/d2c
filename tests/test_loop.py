"""Tests for Phase 2: Agent Loop & Context Assembly."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from d2c.config import Config
from d2c.context import (
    SystemContext,
    assembleMessages,
    estimate_tokens,
    getSystemContext,
    getSystemPrompt,
    getUserContext,
)
from d2c.loop import (
    LoopConfig,
    StopEvent,
    StubHookRegistry,
    StubPermissionEngine,
    TextResponse,
    ToolExecutionEvent,
    _assistant_message_with_tools,
    _build_anthropic_messages,
    _extract_tool_uses,
    _response_text,
    _tool_result_message,
    dispatchTools,
    partitionToolCalls,
    queryLoop,
)
from d2c.tools import PermissionCategory, Tool, ToolResult, ToolUse

# ── Mock model response ──────────────────────────────────────────────


class MockContentBlock:
    def __init__(self, block_type: str, text: str = "", **kwargs):
        self.type = block_type
        self.text = text
        for k, v in kwargs.items():
            setattr(self, k, v)


@dataclass
class MockResponse:
    content: list[MockContentBlock]
    stop_reason: str = "end_turn"


def make_text_response(text: str) -> MockResponse:
    return MockResponse(content=[MockContentBlock("text", text=text)])


def make_tool_use_response(tool_uses: list[tuple[str, str, dict]]) -> MockResponse:
    """Create a mock response with tool_use blocks.

    Each tuple: (id, name, input)
    """
    blocks = [
        MockContentBlock("tool_use", id=tu_id, name=NameVal(name), input=DictVal(inp))
        for tu_id, name, inp in tool_uses
    ]
    return MockResponse(content=blocks)


class NameVal:
    def __init__(self, val: str):
        self._val = val

    def __eq__(self, other):
        if hasattr(other, "value"):
            return self._val == other.value
        return self._val == other


class DictVal(dict):
    pass


# ── Mock tool ────────────────────────────────────────────────────────


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


class MockWriteTool(Tool):
    name = "Write"
    description = "Write a file"
    input_schema = {
        "type": "object",
        "properties": {"file_path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["file_path", "content"],
    }
    category = PermissionCategory.WRITE
    is_concurrent_safe = False

    async def execute(self, file_path: str, content: str, **kwargs) -> ToolResult:
        return ToolResult(output=f"wrote {file_path}")


def make_loop_config(model="deepseek-v4-pro", max_turns=25, tools=None) -> LoopConfig:
    config = Config(model=model, max_turns=max_turns)
    return LoopConfig(
        system_prompt="You are a helpful agent.",
        user_context="Today's date: 2026-06-30",
        model=model,
        max_turns=max_turns,
        tools=tools or [MockReadTool(), MockWriteTool()],
        permission_engine=StubPermissionEngine(),
        hooks=StubHookRegistry(),
        config=config,
        deepseek_api_key="test-key",
    )


# ── Context tests ────────────────────────────────────────────────────


def test_get_system_prompt():
    prompt = getSystemPrompt()
    assert "d2c" in prompt
    assert "tools" in prompt.lower()


def test_system_context_format():
    ctx = SystemContext(
        git_status="on branch 'main'", platform="Linux", cwd="/home/user/project", date="2026-06-30"
    )
    formatted = ctx.format()
    assert "main" in formatted
    assert "Linux" in formatted
    assert "/home/user/project" in formatted


def test_get_system_context():
    config = Config.load()
    ctx = getSystemContext(config)
    assert ctx.platform in ("Windows", "Linux", "Darwin")
    assert ctx.cwd == str(config.cwd)


def test_get_user_context():
    config = Config.load()
    ctx = getUserContext(config)
    assert "2026" in ctx


def test_assemble_messages():
    prompt = "You are d2c."
    ctx = SystemContext(git_status=None, platform="Linux", cwd="/tmp", date="2026-06-30")
    user_ctx = "Today: 2026-06-30"
    history = [{"role": "user", "content": "hello"}]

    full_prompt, messages = assembleMessages(prompt, ctx, user_ctx, history)

    assert "/tmp" in full_prompt
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == user_ctx
    assert messages[1] == history[0]


def test_estimate_tokens():
    messages = [
        {"role": "user", "content": "hello world"},
        {"role": "assistant", "content": "hi there"},
    ]
    tokens = estimate_tokens(messages)
    # BPE: ~2+2 content tokens + ~13 overhead ≈ 17
    assert 5 <= tokens <= 25


# ── Message format tests ─────────────────────────────────────────────


def test_build_anthropic_messages_simple():
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    result = _build_anthropic_messages(messages)
    assert len(result) == 2
    assert result[0]["role"] == "user"
    assert isinstance(result[0]["content"], list)
    assert result[0]["content"][0] == {"type": "text", "text": "hello"}


def test_build_anthropic_messages_tool_result():
    messages = [
        {"role": "tool", "content": "output", "tool_use_id": "tu_1"},
    ]
    result = _build_anthropic_messages(messages)
    assert len(result) == 1
    assert result[0]["role"] == "user"  # Anthropic uses user role for tool results
    assert result[0]["content"][0]["type"] == "tool_result"
    assert result[0]["content"][0]["tool_use_id"] == "tu_1"


def test_build_anthropic_messages_assistant_with_blocks():
    messages = [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "hello"},
                {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/x"}},
            ],
        },
    ]
    result = _build_anthropic_messages(messages)
    assert len(result) == 1
    assert result[0]["content"] == messages[0]["content"]


def test_build_anthropic_messages_merges_consecutive_tool_results():
    """Regression: an assistant turn with multiple tool_use blocks (e.g.
    concurrent-safe reads) must produce ONE user message containing all the
    tool_result blocks immediately after it — not one user message per
    tool. Anthropic-compatible APIs reject a turn where a later tool_use id
    has no tool_result in the very next message, which is what happens if
    each tool result becomes its own separate user message."""
    messages = [
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "tu1", "name": "Read", "input": {}},
                {"type": "tool_use", "id": "tu2", "name": "Grep", "input": {}},
                {"type": "tool_use", "id": "tu3", "name": "Glob", "input": {}},
            ],
        },
        {"role": "tool", "content": "read output", "tool_use_id": "tu1"},
        {"role": "tool", "content": "grep output", "tool_use_id": "tu2"},
        {"role": "tool", "content": "glob output", "tool_use_id": "tu3"},
        {"role": "user", "content": "thanks"},
    ]
    result = _build_anthropic_messages(messages)

    # assistant, ONE merged user/tool_result message, final user message.
    assert len(result) == 3
    assert result[0]["role"] == "assistant"
    assert result[1]["role"] == "user"
    tool_result_blocks = result[1]["content"]
    assert [b["type"] for b in tool_result_blocks] == ["tool_result"] * 3
    assert [b["tool_use_id"] for b in tool_result_blocks] == ["tu1", "tu2", "tu3"]
    assert [b["content"] for b in tool_result_blocks] == [
        "read output",
        "grep output",
        "glob output",
    ]
    assert result[2] == {"role": "user", "content": [{"type": "text", "text": "thanks"}]}


def test_build_anthropic_messages_does_not_merge_nonadjacent_tool_results():
    """Two single-tool turns stay as two separate tool_result messages —
    only messages that were consecutive "tool" entries in the source list
    get merged."""
    messages = [
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "tu1", "name": "Read", "input": {}}],
        },
        {"role": "tool", "content": "first output", "tool_use_id": "tu1"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "tu2", "name": "Read", "input": {}}],
        },
        {"role": "tool", "content": "second output", "tool_use_id": "tu2"},
    ]
    result = _build_anthropic_messages(messages)
    assert len(result) == 4
    assert [r["role"] for r in result] == ["assistant", "user", "assistant", "user"]
    assert result[1]["content"] == [
        {"type": "tool_result", "tool_use_id": "tu1", "content": "first output"}
    ]
    assert result[3]["content"] == [
        {"type": "tool_result", "tool_use_id": "tu2", "content": "second output"}
    ]


def test_extract_tool_uses():
    block = MockContentBlock("tool_use", id="tu_1", name="Read", input={"file_path": "/x.txt"})
    response = MockResponse(content=[block])
    result = _extract_tool_uses(response)
    assert len(result) == 1
    assert result[0].name == "Read"
    assert "file_path" in result[0].input


def test_extract_tool_uses_empty():
    response = make_text_response("hello")
    result = _extract_tool_uses(response)
    assert len(result) == 0


def test_response_text():
    response = make_text_response("hello world")
    assert _response_text(response) == "hello world"


def test_assistant_message_with_tools():
    tus = [ToolUse(id="t1", name="Read", input={"file_path": "/x"})]
    msg = _assistant_message_with_tools("let me read", tus)
    assert msg["role"] == "assistant"
    assert msg["content"][0]["type"] == "text"
    assert msg["content"][1]["type"] == "tool_use"


def test_tool_result_message():
    tu = ToolUse(id="t1", name="Read", input={"file_path": "/x"})
    result = ToolResult(output="content here")
    msg = _tool_result_message(tu, result)
    assert msg["role"] == "tool"
    assert msg["tool_use_id"] == "t1"
    assert msg["content"] == "content here"


# ── Tool dispatch tests ──────────────────────────────────────────────


def test_partition_concurrent_and_write():
    tools_map = {"Read": MockReadTool(), "Write": MockWriteTool()}
    tool_uses = [
        ToolUse(id="1", name="Read", input={"file_path": "/a"}),
        ToolUse(id="2", name="Read", input={"file_path": "/b"}),
        ToolUse(id="3", name="Write", input={"file_path": "/c", "content": "x"}),
        ToolUse(id="4", name="Read", input={"file_path": "/d"}),
    ]
    partitions = partitionToolCalls(tool_uses, tools_map)
    assert len(partitions) == 3
    # First partition: 2 concurrent reads
    assert len(partitions[0]) == 2
    # Second partition: 1 write (serialized)
    assert len(partitions[1]) == 1
    # Third partition: 1 read (after write barrier)
    assert len(partitions[2]) == 1


def test_partition_all_read():
    tools_map = {"Read": MockReadTool()}
    tool_uses = [
        ToolUse(id="1", name="Read", input={"file_path": "/a"}),
        ToolUse(id="2", name="Read", input={"file_path": "/b"}),
    ]
    partitions = partitionToolCalls(tool_uses, tools_map)
    assert len(partitions) == 1
    assert len(partitions[0]) == 2


def test_partition_all_write():
    tools_map = {"Write": MockWriteTool()}
    tool_uses = [
        ToolUse(id="1", name="Write", input={"file_path": "/a", "content": "x"}),
        ToolUse(id="2", name="Write", input={"file_path": "/b", "content": "y"}),
    ]
    partitions = partitionToolCalls(tool_uses, tools_map)
    assert len(partitions) == 2
    assert len(partitions[0]) == 1
    assert len(partitions[1]) == 1


@pytest.mark.asyncio
async def test_dispatch_tools():
    tools_map = {"Read": MockReadTool()}
    from d2c.loop import LoopState

    state = LoopState(messages=[])

    tool_uses = [ToolUse(id="1", name="Read", input={"file_path": "/test.txt"})]
    events = []
    async for event in dispatchTools(tool_uses, tools_map, state):
        events.append(event)

    assert len(events) == 1
    assert "test.txt" in events[0].result.output
    assert len(state.messages) == 1


# ── queryLoop tests (mocked model) ───────────────────────────────────


@pytest.mark.asyncio
async def test_loop_text_only_response():
    """Model returns text only → loop stops after 1 turn."""
    config = make_loop_config()

    with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            return_value=make_text_response("Hello, how can I help?")
        )
        mock_cls.return_value = mock_client

        events = []
        messages = [{"role": "user", "content": "hi"}]
        async for event in queryLoop(config, messages):
            events.append(event)

    assert len(events) == 1
    assert isinstance(events[0], TextResponse)
    assert "hello" in events[0].text.lower()


@pytest.mark.asyncio
async def test_loop_tool_use_then_text():
    """Model uses a tool, then returns text."""
    config = make_loop_config()

    with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
        mock_client = MagicMock()

        # First call: tool_use → Read /test.txt
        call_count = [0]

        async def side_effect(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # Wait, I need to construct a tool_use response properly
                block = MockContentBlock(
                    "tool_use", id="tu_1", name="Read", input={"file_path": "/test.txt"}
                )
                return MockResponse(content=[block])
            else:
                return make_text_response("I read the file. It contains hello world.")

        mock_client.messages.create = AsyncMock(side_effect=side_effect)
        mock_cls.return_value = mock_client

        events = []
        messages = [{"role": "user", "content": "read /test.txt"}]
        async for event in queryLoop(config, messages):
            events.append(event)

    # First event: tool execution, Second event: text response
    assert len(events) >= 2
    assert isinstance(events[0], ToolExecutionEvent)
    assert events[0].tool_use.name == "Read"
    assert isinstance(events[-1], TextResponse)


@pytest.mark.asyncio
async def test_loop_multi_tool_turn_sends_well_formed_followup_request():
    """Regression: a turn with multiple concurrent-safe tool_use blocks must
    produce a single well-formed follow-up request — every tool_use id from
    the previous assistant turn needs a tool_result in the very next
    message, not spread across several consecutive user messages (real
    Anthropic-compatible APIs reject that with a 400)."""
    config = make_loop_config()

    with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
        mock_client = MagicMock()
        call_count = [0]
        captured_messages: list[list[dict]] = []

        async def side_effect(**kwargs):
            captured_messages.append(kwargs["messages"])
            call_count[0] += 1
            if call_count[0] == 1:
                blocks = [
                    MockContentBlock(
                        "tool_use", id="tu_1", name="Read", input={"file_path": "/a.txt"}
                    ),
                    MockContentBlock(
                        "tool_use", id="tu_2", name="Read", input={"file_path": "/b.txt"}
                    ),
                ]
                return MockResponse(content=blocks)
            return make_text_response("Read both files.")

        mock_client.messages.create = AsyncMock(side_effect=side_effect)
        mock_cls.return_value = mock_client

        events = []
        messages = [{"role": "user", "content": "read both files"}]
        async for event in queryLoop(config, messages):
            events.append(event)

    assert call_count[0] == 2
    # The second request is the one carrying the tool results.
    followup = captured_messages[1]
    assistant_idx = next(i for i, m in enumerate(followup) if m.get("role") == "assistant")
    tool_use_ids = {
        b["id"] for b in followup[assistant_idx]["content"] if b.get("type") == "tool_use"
    }
    assert tool_use_ids == {"tu_1", "tu_2"}

    # Every tool_use id must have its tool_result in the immediately
    # following message — a single message, not two separate ones.
    next_msg = followup[assistant_idx + 1]
    assert next_msg["role"] == "user"
    result_ids = {b["tool_use_id"] for b in next_msg["content"] if b.get("type") == "tool_result"}
    assert result_ids == tool_use_ids


@pytest.mark.asyncio
async def test_loop_max_turns():
    """Loop stops when max turns reached."""
    config = make_loop_config(max_turns=2)

    with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
        mock_client = MagicMock()

        # Always return tool_use (never stop naturally)
        async def side_effect(**kwargs):
            block = MockContentBlock(
                "tool_use", id="tu_1", name="Read", input={"file_path": "/test.txt"}
            )
            return MockResponse(content=[block])

        mock_client.messages.create = AsyncMock(side_effect=side_effect)
        mock_cls.return_value = mock_client

        events = []
        messages = [{"role": "user", "content": "test"}]
        async for event in queryLoop(config, messages):
            events.append(event)

    # Last event should be stop
    assert isinstance(events[-1], StopEvent)
    assert events[-1].reason == "max_turns"


@pytest.mark.asyncio
async def test_loop_no_api_key():
    """Loop stops immediately if no API key."""
    config = make_loop_config()
    config.deepseek_api_key = None

    events = []
    messages = [{"role": "user", "content": "hi"}]
    async for event in queryLoop(config, messages):
        events.append(event)

    assert len(events) == 1
    assert isinstance(events[0], TextResponse)
    assert "DEEPSEEK_API_KEY" in events[0].text


@pytest.mark.asyncio
async def test_loop_api_error():
    """Loop handles API errors gracefully."""
    config = make_loop_config()

    with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(side_effect=Exception("Connection refused"))
        mock_cls.return_value = mock_client

        events = []
        messages = [{"role": "user", "content": "hi"}]
        async for event in queryLoop(config, messages):
            events.append(event)

    assert len(events) == 1
    assert isinstance(events[0], TextResponse)
    assert "Error" in events[0].text


@pytest.mark.asyncio
async def test_loop_unknown_tool():
    """Loop handles unknown tool gracefully."""
    config = make_loop_config()

    with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
        mock_client = MagicMock()

        call_count = [0]

        async def side_effect(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                block = MockContentBlock("tool_use", id="tu_1", name="UnknownTool", input={})
                return MockResponse(content=[block])
            else:
                return make_text_response("That tool doesn't exist, let me try another approach.")

        mock_client.messages.create = AsyncMock(side_effect=side_effect)
        mock_cls.return_value = mock_client

        events = []
        messages = [{"role": "user", "content": "use unknown tool"}]
        async for event in queryLoop(config, messages):
            events.append(event)

    assert isinstance(events[0], ToolExecutionEvent)
    assert events[0].result.error is True
    assert "unknown" in events[0].result.output.lower()
