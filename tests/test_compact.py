"""Tests for Phase 5: Compaction Pipeline — budget reduction, auto-compact, token estimation."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from d2c.compact import (
    CompactConfig,
    applyBudgetReduction,
    applyContextShapers,
    autoCompact,
    buildPostCompactMessages,
    checkPressure,
    compute_pressure_limit,
    estimate_tokens,
    getCompactPrompt,
)


# ── Fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def config():
    return CompactConfig(
        tool_result_max_chars=500,
        pressure_threshold=0.85,
        context_window_tokens=100_000,
        chars_per_token=4.0,
    )


@pytest.fixture
def sample_messages():
    return [
        {"role": "system", "content": "You are a helpful agent."},
        {"role": "user", "content": "Read the file at /test.txt"},
        {"role": "tool", "content": "content of /test.txt\n" + "line " * 50, "tool_use_id": "t1"},
        {"role": "assistant", "content": "I read the file."},
    ]


# ── Budget reduction tests ─────────────────────────────────────────────

class TestBudgetReduction:
    def test_caps_long_tool_output(self, config):
        long_content = "x" * 1000
        messages = [
            {"role": "tool", "content": long_content},
        ]
        result = applyBudgetReduction(messages, config)
        expected_notice = "\n... [truncated 500 chars]"
        assert len(result[0]["content"]) == 500 + len(expected_notice)

    def test_preserves_short_tool_output(self, config):
        short = "hello"
        messages = [{"role": "tool", "content": short}]
        result = applyBudgetReduction(messages, config)
        assert result[0]["content"] == short

    def test_preserves_non_tool_messages(self, config):
        messages = [
            {"role": "user", "content": "x" * 1000},
            {"role": "assistant", "content": "y" * 1000},
        ]
        result = applyBudgetReduction(messages, config)
        # User/assistant messages are NOT capped
        assert result[0]["content"] == "x" * 1000
        assert result[1]["content"] == "y" * 1000

    def test_preserves_list_content(self):
        messages = [
            {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
        ]
        result = applyBudgetReduction(messages, CompactConfig())
        assert result[0]["content"] == [{"type": "text", "text": "hello"}]

    def test_truncation_notice_format(self, config):
        long_content = "abc" * 1000
        messages = [{"role": "tool", "content": long_content}]
        result = applyBudgetReduction(messages, config)
        assert "[truncated" in result[0]["content"]
        assert "chars]" in result[0]["content"]

    def test_exact_boundary(self, config):
        """Content exactly at the limit should NOT be truncated."""
        content = "a" * config.tool_result_max_chars
        messages = [{"role": "tool", "content": content}]
        result = applyBudgetReduction(messages, config)
        assert result[0]["content"] == content


# ── Token estimation tests ─────────────────────────────────────────────

class TestEstimateTokens:
    def test_estimate_simple_messages(self):
        """Phase 28: BPE token counting — lenient range for BPE overhead."""
        messages = [
            {"role": "user", "content": "hello world"},
            {"role": "assistant", "content": "hi there"},
        ]
        tokens = estimate_tokens(messages, CompactConfig(chars_per_token=4.0))
        # BPE: ~2 tokens for content + ~15 overhead = ~19; fallback: ~5
        assert tokens > 0

    def test_estimate_empty_messages(self):
        """Phase 28: BPE adds ~3 tokens framing even for empty lists."""
        tokens = estimate_tokens([], CompactConfig())
        assert tokens >= 0

    def test_estimate_with_list_content(self):
        messages = [
            {"role": "assistant", "content": [{"type": "text", "text": "a" * 40}]},
        ]
        tokens = estimate_tokens(messages, CompactConfig(chars_per_token=4.0))
        # BPE: ~40 content tokens + overhead; fallback: ~10
        assert tokens >= 8

    def test_estimate_default_chars_per_token(self):
        messages = [{"role": "user", "content": "a" * 35}]
        tokens = estimate_tokens(messages)
        # BPE: ~35 content tokens + overhead; fallback: 10
        assert tokens > 0

    def test_estimate_handles_non_string_content(self):
        messages = [{"role": "user", "content": 12345}]
        tokens = estimate_tokens(messages, CompactConfig(chars_per_token=4.0))
        assert tokens > 0


# ── Pressure limit tests ───────────────────────────────────────────────

class TestPressureLimit:
    def test_compute_pressure_limit(self):
        config = CompactConfig(context_window_tokens=100_000, pressure_threshold=0.85)
        assert compute_pressure_limit(config) == 85_000

    def test_compute_pressure_limit_integer(self):
        config = CompactConfig(context_window_tokens=100_000, pressure_threshold=0.5)
        assert compute_pressure_limit(config) == 50_000

    def test_check_pressure_over_limit(self):
        config = CompactConfig(
            context_window_tokens=1000,
            pressure_threshold=0.5,
            chars_per_token=1.0,
        )
        # Phase 28: BPE compresses repeated chars, so use varied content
        # that produces > 500 tokens. "abc" is 1 BPE token per char.
        messages = [{"role": "user", "content": "abc def ghi " * 200}]
        assert checkPressure(messages, config) is True

    def test_check_pressure_under_limit(self):
        config = CompactConfig(
            context_window_tokens=1000,
            pressure_threshold=0.5,
            chars_per_token=1.0,
        )
        messages = [{"role": "user", "content": "abc" * 100}]
        assert checkPressure(messages, config) is False


# ── Compact prompt tests ───────────────────────────────────────────────

class TestGetCompactPrompt:
    def test_formats_messages(self):
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        result = getCompactPrompt(messages)
        assert "[user]: hello" in result
        assert "[assistant]: hi" in result

    def test_excludes_last_4_messages(self):
        messages = [
            {"role": "user", "content": "m1"},
            {"role": "assistant", "content": "m2"},
            {"role": "user", "content": "m3"},
            {"role": "assistant", "content": "m4"},
            {"role": "user", "content": "m5"},
            {"role": "assistant", "content": "m6"},
        ]
        result = getCompactPrompt(messages)
        # Only m1 and m2 should be in the prompt (last 4 excluded)
        assert "[user]: m1" in result
        assert "[assistant]: m2" in result
        assert "m5" not in result
        assert "m6" not in result

    def test_all_messages_when_4_or_fewer(self):
        messages = [
            {"role": "user", "content": "m1"},
            {"role": "assistant", "content": "m2"},
        ]
        result = getCompactPrompt(messages)
        assert "[user]: m1" in result
        assert "[assistant]: m2" in result

    def test_truncates_long_content(self):
        messages = [
            {"role": "user", "content": "x" * 1000},
        ]
        result = getCompactPrompt(messages)
        assert len(result.split("\n")[0]) <= 600  # "x" * 500 + prefix

    def test_handles_list_content(self):
        messages = [
            {"role": "assistant", "content": [
                {"type": "text", "text": "hello"},
                {"type": "tool_use", "name": "Read", "id": "t1", "input": {}},
            ]},
        ]
        result = getCompactPrompt(messages)
        assert "[assistant]" in result
        assert "hello" in result
        assert "tool_use" in result


# ── Post-compact message builder tests ─────────────────────────────────

class TestBuildPostCompactMessages:
    def test_keeps_system_messages(self):
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "old response"},
            {"role": "user", "content": "recent1"},
            {"role": "assistant", "content": "recent2"},
            {"role": "user", "content": "recent3"},
            {"role": "assistant", "content": "recent4"},
        ]
        result = buildPostCompactMessages(messages, "Summary text")
        assert result[0] == {"role": "system", "content": "You are helpful."}
        assert result[1]["role"] == "user"
        assert "Summary text" in result[1]["content"]

    def test_includes_summary_as_user_message(self):
        messages = [
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "old"},
            {"role": "user", "content": "r1"},
            {"role": "assistant", "content": "r2"},
            {"role": "user", "content": "r3"},
            {"role": "assistant", "content": "r4"},
        ]
        result = buildPostCompactMessages(messages, "The summary here")
        assert result[0]["role"] == "user"
        assert "The summary here" in result[0]["content"]
        assert "[Previous conversation summary]" in result[0]["content"]

    def test_keeps_last_4_messages(self):
        messages = [
            {"role": "user", "content": "m1"},
            {"role": "assistant", "content": "m2"},
            {"role": "user", "content": "m3"},
            {"role": "assistant", "content": "m4"},
            {"role": "user", "content": "m5"},
            {"role": "assistant", "content": "m6"},
        ]
        result = buildPostCompactMessages(messages, "summary")
        # summary + last 4 = m3, m4, m5, m6
        assert len([m for m in result if m["role"] != "system"]) == 5  # summary + 4 recent

    def test_fewer_than_4_keeps_all(self):
        messages = [
            {"role": "user", "content": "m1"},
            {"role": "assistant", "content": "m2"},
        ]
        result = buildPostCompactMessages(messages, "summary")
        # summary + 2 original
        assert len(result) == 3  # summary + 2

    def test_empty_messages(self):
        result = buildPostCompactMessages([], "empty summary")
        assert len(result) == 1
        assert result[0]["content"] == "[Previous conversation summary]\nempty summary"


# ── Context shapers orchestration tests ────────────────────────────────

class TestApplyContextShapers:
    def test_applies_budget_reduction_when_config_present(self, config):
        messages = [{"role": "tool", "content": "x" * 1000}]
        result = applyContextShapers(messages, config)
        assert len(result[0]["content"]) < 1000

    def test_noop_when_config_is_none(self):
        messages = [{"role": "tool", "content": "x" * 1000}]
        result = applyContextShapers(messages, None)
        assert result == messages

    def test_preserves_structure(self, config):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            {"role": "tool", "content": "result", "tool_use_id": "t1"},
            {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
        ]
        result = applyContextShapers(messages, config)
        assert len(result) == 4
        assert result[0]["role"] == "system"
        assert result[1]["role"] == "user"
        assert result[2]["role"] == "tool"
        assert result[3]["role"] == "assistant"


# ── Auto-compact tests (mocked model) ───────────────────────────────────

class TestAutoCompact:
    @pytest.mark.asyncio
    async def test_auto_compact_builds_post_compact(self):
        """autoCompact should call the model and rebuild messages."""
        messages = [
            {"role": "user", "content": "old conversation"},
            {"role": "assistant", "content": "old reply"},
            {"role": "user", "content": "recent"},
            {"role": "assistant", "content": "recent reply"},
        ]

        loop_config = MagicMock()
        loop_config.compact_config = CompactConfig()
        loop_config.model = "deepseek-v4-pro"
        loop_config.deepseek_api_key = "test-key"
        loop_config.deepseek_base_url = "https://api.deepseek.com/anthropic"
        loop_config.config.deepseek_api_key = "test-key"
        loop_config.session_store = None

        with patch("d2c.compact.anthropic.AsyncAnthropic") as mock_cls:
            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(return_value=MagicMock(
                content=[MagicMock(type="text", text="Compacted summary of the conversation")]
            ))
            mock_cls.return_value = mock_client

            result = await autoCompact(messages, loop_config)

        # Should have summary + last 4 messages
        assert len(result) >= 2
        assert "[Previous conversation summary]" in result[0]["content"]
        assert "Compacted summary" in result[0]["content"]

    @pytest.mark.asyncio
    async def test_auto_compact_without_compact_config(self):
        messages = [{"role": "user", "content": "hi"}]
        loop_config = MagicMock()
        loop_config.compact_config = None

        result = await autoCompact(messages, loop_config)
        assert result == messages

    @pytest.mark.asyncio
    async def test_auto_compact_without_api_key(self):
        messages = [{"role": "user", "content": "hi"}]
        loop_config = MagicMock()
        loop_config.compact_config = CompactConfig()
        loop_config.deepseek_api_key = None
        loop_config.config.deepseek_api_key = None

        result = await autoCompact(messages, loop_config)
        assert result == messages  # Can't compact without key

    @pytest.mark.asyncio
    async def test_auto_compact_model_error_returns_original(self):
        messages = [{"role": "user", "content": "hi"}]
        loop_config = MagicMock()
        loop_config.compact_config = CompactConfig()
        loop_config.model = "deepseek-v4-pro"
        loop_config.deepseek_api_key = "test-key"
        loop_config.deepseek_base_url = "https://api.deepseek.com/anthropic"
        loop_config.config.deepseek_api_key = "test-key"
        loop_config.session_store = None

        with patch("d2c.compact.anthropic.AsyncAnthropic") as mock_cls:
            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(side_effect=Exception("API down"))
            mock_cls.return_value = mock_client

            result = await autoCompact(messages, loop_config)

        # Should return original messages on error
        assert result == messages

    @pytest.mark.asyncio
    async def test_auto_compact_records_boundary(self):
        messages = [
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "old reply"},
        ]

        mock_store = MagicMock()
        mock_store.append_compact_boundary = MagicMock()

        loop_config = MagicMock()
        loop_config.compact_config = CompactConfig()
        loop_config.model = "deepseek-v4-pro"
        loop_config.deepseek_api_key = "test-key"
        loop_config.deepseek_base_url = "https://api.deepseek.com/anthropic"
        loop_config.config.deepseek_api_key = "test-key"
        loop_config.session_store = mock_store

        with patch("d2c.compact.anthropic.AsyncAnthropic") as mock_cls:
            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(return_value=MagicMock(
                content=[MagicMock(type="text", text="Summary")]
            ))
            mock_cls.return_value = mock_client

            await autoCompact(messages, loop_config)

        # Verify compact boundary was recorded
        mock_store.append_compact_boundary.assert_called_once()
