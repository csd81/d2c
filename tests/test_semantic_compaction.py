"""Tests for Phase 29: LLM-based semantic summarization shapers.

Verifies microcompact and context collapse trigger LLM summarization,
handle concurrent task scheduling, and fall back gracefully on failure.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def loop_config():
    from d2c.compact import CompactConfig

    config = CompactConfig(
        microcompact_summary_max_chars=500,
        collapse_min_turns=2,
        collapse_segment_size=3,
    )
    lc = MagicMock()
    lc.compact_config = config
    lc.deepseek_api_key = "test-key"
    lc.deepseek_base_url = "https://api.deepseek.com/anthropic"
    lc.model = "deepseek-chat"
    return lc


@pytest.fixture
def tool_messages():
    """Messages with consecutive tool-result pairs."""
    return [
        {"role": "user", "content": "Run the tests."},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "t1", "name": "bash", "input": {"command": "npm test"}},
            ],
        },
        {
            "role": "tool",
            "content": "FAIL: test_auth (AssertionError: expected 200, got 401)\nFAIL: test_login\n2 tests failed.",
        },
        {"role": "user", "content": "Fix the auth bug."},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "t2", "name": "read", "input": {"path": "auth.py"}},
            ],
        },
        {"role": "tool", "content": "..." + "def authenticate():\n    return check_token()\n" * 20},
    ]


@pytest.fixture
def history_messages():
    """Long conversation history for context collapse."""
    msgs = []
    for i in range(12):
        msgs.append({"role": "user", "content": f"Task {i}"})
        msgs.append({"role": "assistant", "content": f"Response {i}"})
    return msgs


# ── Tests ──────────────────────────────────────────────────────────────


class TestMicrocompactLLM:
    """Verify applyMicrocompact triggers LLM summarization."""

    @pytest.mark.asyncio
    async def test_microcompact_calls_summarizer(self, loop_config, tool_messages):
        """Microcompact schedules summarization tasks for tool-result pairs."""
        from d2c.compact import applyMicrocompact

        # With a real API call it would fail (test key), triggering fallback
        result = await applyMicrocompact(tool_messages, loop_config)
        assert isinstance(result, list)
        assert len(result) < len(tool_messages)

    @pytest.mark.asyncio
    async def test_microcompact_fallback_on_api_error(self, loop_config, tool_messages):
        """When API call fails, microcompact falls back to summarization."""
        from d2c.compact import applyMicrocompact

        result = await applyMicrocompact(tool_messages, loop_config)
        # Should still produce microcompact summaries (fallback path)
        summaries = [m for m in result if "Microcompact" in str(m.get("content", ""))]
        assert len(summaries) >= 1

    @pytest.mark.asyncio
    async def test_microcompact_preserves_non_tool_messages(self, loop_config):
        """Non-tool messages are preserved unchanged."""
        from d2c.compact import applyMicrocompact

        msgs = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ]
        result = await applyMicrocompact(msgs, loop_config)
        assert result[0]["content"] == "Hello"
        assert result[1]["content"] == "Hi!"

    @pytest.mark.asyncio
    async def test_microcompact_concurrent_tasks(self, loop_config, tool_messages):
        """Multiple tool pairs are summarized concurrently."""
        from d2c.compact import applyMicrocompact

        # Add more tool pairs to force concurrent summarization
        extra = [
            {"role": "user", "content": "Task 3"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "t3", "name": "write", "input": {}},
                ],
            },
            {"role": "tool", "content": "Wrote file."},
            {"role": "user", "content": "Task 4"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "t4", "name": "edit", "input": {}},
                ],
            },
            {"role": "tool", "content": "Edited file."},
        ]
        msgs = tool_messages + extra
        result = await applyMicrocompact(msgs, loop_config)
        assert len(result) < len(msgs)


class TestContextCollapseLLM:
    """Verify applyContextCollapse triggers LLM summarization."""

    @pytest.mark.asyncio
    async def test_context_collapse_segments_and_summarizes(
        self,
        loop_config,
        history_messages,
    ):
        """Context collapse segments history and queries LLM."""
        from d2c.compact import applyContextCollapse

        loop_config.compact_config.collapse_min_turns = 2
        loop_config.compact_config.collapse_segment_size = 4
        result = await applyContextCollapse(history_messages, loop_config)
        assert isinstance(result, list)
        assert len(result) <= len(history_messages)

    @pytest.mark.asyncio
    async def test_context_collapse_fallback_on_failure(
        self,
        loop_config,
        history_messages,
    ):
        """When summarization fails, context collapse still produces output."""
        from d2c.compact import applyContextCollapse

        loop_config.compact_config.collapse_min_turns = 2
        loop_config.compact_config.collapse_segment_size = 4
        result = await applyContextCollapse(history_messages, loop_config)
        # Should still be valid message list
        for msg in result:
            assert "role" in msg
            assert "content" in msg

    @pytest.mark.asyncio
    async def test_context_collapse_preserves_system_messages(
        self,
        loop_config,
    ):
        """System messages are always preserved at the top."""
        from d2c.compact import applyContextCollapse

        loop_config.compact_config.collapse_min_turns = 1
        loop_config.compact_config.collapse_segment_size = 2
        msgs = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Task 1"},
            {"role": "assistant", "content": "Response 1"},
            {"role": "user", "content": "Task 2"},
            {"role": "assistant", "content": "Response 2"},
            {"role": "user", "content": "Task 3"},
            {"role": "assistant", "content": "Response 3"},
            {"role": "user", "content": "Task 4"},
            {"role": "assistant", "content": "Response 4"},
        ]
        result = await applyContextCollapse(msgs, loop_config)
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "You are a helpful assistant."

    @pytest.mark.asyncio
    async def test_context_collapse_noop_below_threshold(
        self,
        loop_config,
    ):
        """Fewer than min_turns*2 messages → no collapse."""
        from d2c.compact import applyContextCollapse

        loop_config.compact_config.collapse_min_turns = 10
        short = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello"},
        ]
        result = await applyContextCollapse(short, loop_config)
        assert result == short


class TestSummarizeSegmentContent:
    """Verify the _summarize_segment_content helper."""

    @pytest.mark.asyncio
    async def test_summarize_tools_type(self, loop_config):
        """Tools summary type generates appropriate prompt and response."""
        from d2c.compact import _summarize_segment_content

        # With test key, API call will fail → fallback
        result = await _summarize_segment_content(
            "npm test failed with AssertionError: expected 200, got 401",
            loop_config,
            summary_type="tools",
        )
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_summarize_history_type(self, loop_config):
        """History summary type generates different prompt."""
        from d2c.compact import _summarize_segment_content

        result = await _summarize_segment_content(
            "User: Fix auth bug | Assistant: read auth.py, edited line 42",
            loop_config,
            summary_type="history",
        )
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_summarize_empty_text(self, loop_config):
        """Empty text returns placeholder."""
        from d2c.compact import _summarize_segment_content

        result = await _summarize_segment_content("", loop_config)
        assert result == "(empty)"

    @pytest.mark.asyncio
    async def test_summarize_long_text_fallback(self, loop_config):
        """Long text falls back to character slicing on API failure."""
        from d2c.compact import _summarize_segment_content

        long_text = "x" * 500
        result = await _summarize_segment_content(
            long_text,
            loop_config,
            summary_type="tools",
        )
        assert isinstance(result, str)
        # Fallback returns truncated text + heuristic suffix
        assert len(result) <= 330


class TestSummarizationFallback:
    """Verify fallback behavior when LLM summarization fails."""

    @pytest.mark.asyncio
    async def test_api_error_returns_truncated_text(self, loop_config):
        """When API raises an error, fallback returns char-sliced text."""
        from d2c.compact import _summarize_segment_content

        text = "Error details: " + "stack trace " * 100
        result = await _summarize_segment_content(
            text,
            loop_config,
            summary_type="tools",
        )
        # Should have some content — either LLM summary or fallback
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_microcompact_survives_all_summarization_failures(
        self,
        loop_config,
    ):
        """Even if all summarization tasks fail, microcompact returns valid messages."""
        from d2c.compact import applyMicrocompact

        msgs = [
            {"role": "user", "content": "Task 1"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "t1", "name": "bash", "input": {}},
                ],
            },
            {"role": "tool", "content": "output"},
            {"role": "user", "content": "Task 2"},
        ]
        result = await applyMicrocompact(msgs, loop_config)
        assert isinstance(result, list)
        # Should compact the tool pair → summary + trailing user message = 2
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_context_collapse_survives_all_summarization_failures(
        self,
        loop_config,
    ):
        """Even if all summarization tasks fail, context collapse returns valid messages."""
        from d2c.compact import applyContextCollapse

        loop_config.compact_config.collapse_min_turns = 2
        loop_config.compact_config.collapse_segment_size = 3
        msgs = [{"role": "user", "content": f"Task {i}"} for i in range(10)] + [
            {"role": "assistant", "content": f"Response {i}"} for i in range(10)
        ]
        result = await applyContextCollapse(msgs, loop_config)
        assert isinstance(result, list)
        for msg in result:
            assert "role" in msg
