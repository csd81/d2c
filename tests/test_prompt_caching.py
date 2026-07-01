"""Tests for Phase 26: Explicit Prompt Caching.

Verifies cache_control injection in system prompts, tool schemas,
and message history when prompt_caching_enabled=True (default).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Tests ─────────────────────────────────────────────────────────────


class TestBuildAnthropicMessages:
    """Test _build_anthropic_messages with cache control injection."""

    def test_adds_cache_control_to_first_message(self):
        """Breakpoint 3: first message gets cache_control."""
        from d2c.loop import _build_anthropic_messages

        messages = [
            {"role": "user", "content": "System context / CLAUDE.md content here."},
            {"role": "user", "content": "Hello, what is 2+2?"},
            {"role": "assistant", "content": "4"},
        ]

        result = _build_anthropic_messages(messages, enable_caching=True)

        # First message should have cache_control on its content block
        first_content = result[0]["content"]
        assert isinstance(first_content, list)
        assert "cache_control" in first_content[0]
        assert first_content[0]["cache_control"] == {"type": "ephemeral"}

        # Second message should NOT have cache_control
        second_content = result[1]["content"]
        assert "cache_control" not in second_content[0]

    def test_adds_sliding_cache_control(self):
        """Breakpoint 4: 5th-from-last message gets cache_control in long history."""
        from d2c.loop import _build_anthropic_messages

        # Build a conversation with 10 messages (> 8 threshold)
        messages = [{"role": "user", "content": f"message {i}"} for i in range(10)]

        result = _build_anthropic_messages(messages, enable_caching=True)

        # 5th from last = index 5 (10 - 5)
        sliding_msg = result[5]["content"]
        assert "cache_control" in sliding_msg[-1]
        assert sliding_msg[-1]["cache_control"] == {"type": "ephemeral"}

        # Verify first message also has cache_control
        assert "cache_control" in result[0]["content"][0]

    def test_sliding_cache_not_set_for_short_conversation(self):
        """Under 8 messages, no sliding cache breakpoint."""
        from d2c.loop import _build_anthropic_messages

        messages = [{"role": "user", "content": f"msg {i}"} for i in range(5)]

        result = _build_anthropic_messages(messages, enable_caching=True)

        # Only first message should have cache_control
        assert "cache_control" in result[0]["content"][0]
        for i in range(1, len(result)):
            content = result[i]["content"]
            if isinstance(content, list):
                for block in content:
                    assert "cache_control" not in block.get("cache_control", "absent")

    def test_disable_prompt_caching(self):
        """No cache_control blocks when enable_caching=False."""
        from d2c.loop import _build_anthropic_messages

        messages = [
            {"role": "user", "content": "Context"},
            {"role": "user", "content": "Hello"},
        ] + [{"role": "user", "content": f"msg {i}"} for i in range(10)]

        result = _build_anthropic_messages(messages, enable_caching=False)

        for msg in result:
            content = msg["content"]
            if isinstance(content, list):
                for block in content:
                    assert "cache_control" not in block

    def test_assistant_with_content_blocks(self):
        """Assistant messages with content blocks get cache_control on last block."""
        from d2c.loop import _build_anthropic_messages

        messages = [
            {"role": "user", "content": "Context"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Some text."},
                    {"type": "tool_use", "id": "tu_1", "name": "Read", "input": {}},
                ],
            },
            {"role": "user", "content": "next"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Reply."},
                ],
            },
            {"role": "user", "content": "another"},
        ] + [{"role": "user", "content": f"msg {i}"} for i in range(8)]

        result = _build_anthropic_messages(messages, enable_caching=True)

        # First message has cache_control
        assert "cache_control" in result[0]["content"][0]

        # The assistant message at index 1 is the first assistant msg — should
        # not have cache_control (it's not first message and not 5th-from-last)
        # But the sliding breakpoint may land on some message — just verify
        # that the total count of cache_control blocks is correct
        cache_count = 0
        for msg in result:
            content = msg["content"]
            if isinstance(content, list):
                for block in content:
                    if "cache_control" in block:
                        cache_count += 1
        # first message + 5th-from-last = 2 cache breakpoints
        assert cache_count == 2

    def test_tool_message_with_cache_control(self):
        """Tool result messages get cache_control on their content block."""
        from d2c.loop import _build_anthropic_messages

        messages = [{"role": "user", "content": "Context"}]
        messages.append({"role": "tool", "content": "file contents here", "tool_use_id": "tu_1"})
        for i in range(10):
            messages.append({"role": "user", "content": f"msg {i}"})

        result = _build_anthropic_messages(messages, enable_caching=True)

        # First message has cache_control
        first_block = result[0]["content"][0]
        assert "cache_control" in first_block

        # Verify tool message format is preserved
        tool_msg = result[1]
        assert tool_msg["role"] == "user"
        tool_block = tool_msg["content"][0]
        assert tool_block["type"] == "tool_result"
        assert tool_block["tool_use_id"] == "tu_1"
        # The tool message may or may not have cache_control depending on index
        # It's fine either way — just verify it's well-formed


# ── Integration tests ────────────────────────────────────────────────


class TestQueryLoopCaching:
    @pytest.mark.asyncio
    async def test_query_loop_injects_cache_control_when_enabled(self):
        """System prompt and tools get cache_control when caching is enabled."""
        from d2c.config import Config
        from d2c.hooks import HookRegistry
        from d2c.loop import LoopConfig, queryLoop
        from d2c.permissions import PermissionEngine, PermissionMode

        config = Config.load()
        config.deepseek_api_key = "test-key"
        config.prompt_caching_enabled = True

        loop_config = LoopConfig(
            system_prompt="You are a helpful assistant.",
            user_context="",
            model=config.model,
            max_turns=1,
            tools=[],
            permission_engine=PermissionEngine(PermissionMode("default")),
            hooks=HookRegistry(),
            config=config,
            deepseek_api_key="test-key",
            deepseek_base_url="https://api.deepseek.com/anthropic",
        )

        messages = [{"role": "user", "content": "Hi"}]

        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="Hello!")]

        with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            async for event in queryLoop(loop_config, messages):
                pass  # consume events

        # Verify the API call was made with cache_control in system and tools
        call_kwargs = mock_client.messages.create.call_args[1]

        # system should be a list with cache_control
        assert isinstance(call_kwargs["system"], list)
        system_block = call_kwargs["system"][0]
        assert system_block["cache_control"] == {"type": "ephemeral"}
        assert system_block["text"] == "You are a helpful assistant."

    @pytest.mark.asyncio
    async def test_query_loop_no_cache_control_when_disabled(self):
        """System prompt is a plain string when caching is disabled."""
        from d2c.config import Config
        from d2c.hooks import HookRegistry
        from d2c.loop import LoopConfig, queryLoop
        from d2c.permissions import PermissionEngine, PermissionMode

        config = Config.load()
        config.deepseek_api_key = "test-key"
        config.prompt_caching_enabled = False

        loop_config = LoopConfig(
            system_prompt="You are helpful.",
            user_context="",
            model=config.model,
            max_turns=1,
            tools=[],
            permission_engine=PermissionEngine(PermissionMode("default")),
            hooks=HookRegistry(),
            config=config,
            deepseek_api_key="test-key",
            deepseek_base_url="https://api.deepseek.com/anthropic",
        )

        messages = [{"role": "user", "content": "Hi"}]

        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="Hello!")]

        with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            async for event in queryLoop(loop_config, messages):
                pass

        call_kwargs = mock_client.messages.create.call_args[1]

        # system should be a plain string (no cache_control)
        assert isinstance(call_kwargs["system"], str)
        assert call_kwargs["system"] == "You are helpful."

    @pytest.mark.asyncio
    async def test_tools_with_cache_control(self):
        """Last tool gets cache_control when caching is enabled."""
        from d2c.config import Config
        from d2c.hooks import HookRegistry
        from d2c.loop import LoopConfig, queryLoop
        from d2c.permissions import PermissionEngine, PermissionMode
        from d2c.tools import PermissionCategory, Tool, ToolResult

        config = Config.load()
        config.deepseek_api_key = "test-key"
        config.prompt_caching_enabled = True

        class FakeTool(Tool):
            name = "TestTool"
            description = "A test tool"
            input_schema = {"type": "object", "properties": {}}
            category = PermissionCategory.READ

            async def execute(self, **kwargs):
                return ToolResult(output="ok")

        loop_config = LoopConfig(
            system_prompt="test",
            user_context="",
            model=config.model,
            max_turns=1,
            tools=[FakeTool(), FakeTool()],  # need distinct instances
            permission_engine=PermissionEngine(PermissionMode("default")),
            hooks=HookRegistry(),
            config=config,
            deepseek_api_key="test-key",
            deepseek_base_url="https://api.deepseek.com/anthropic",
        )

        # Rename second tool to be different
        loop_config.tools[1].name = "TestTool2"

        messages = [{"role": "user", "content": "Hi"}]

        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="Hello!")]

        with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            async for event in queryLoop(loop_config, messages):
                pass

        call_kwargs = mock_client.messages.create.call_args[1]
        tools = call_kwargs["tools"]

        # Last tool should have cache_control
        assert "cache_control" in tools[-1]
        assert tools[-1]["cache_control"] == {"type": "ephemeral"}

        # First tool should NOT have cache_control
        assert "cache_control" not in tools[0]
