"""Tests for Phase 30: Cache-Aligned Compaction Boundaries.

Verifies that snip and context collapse align cut points to 1024-token
cache block boundaries, inject cache_control at the boundary, and no-op
when total context is below the minimum cacheable size.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from d2c.compact import (
    CompactConfig,
    CACHE_BLOCK_SIZE,
    _find_cache_alignment_point,
    _inject_cache_control,
    _compute_system_tools_tokens,
    applySnip,
    estimate_tokens,
)


# ── Fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def config():
    return CompactConfig(
        snip_keep_last=6,
        collapse_min_turns=2,
        collapse_segment_size=3,
        chars_per_token=4.0,
    )


@pytest.fixture
def loop_config(config):
    lc = MagicMock()
    lc.compact_config = config
    lc.deepseek_api_key = "test-key"
    lc.deepseek_base_url = "https://api.deepseek.com/anthropic"
    lc.model = "deepseek-chat"
    return lc


# ── Cache alignment point tests ────────────────────────────────────────

class TestFindCacheAlignmentPoint:
    def test_finds_best_1024_alignment(self, config):
        """Confirm splitting logic correctly identifies message indices
        that minimize remainder modulo 1024."""
        # Varied content to avoid BPE compression; each message is substantial
        msgs = [
            {"role": "user", "content": f"msg {i} " + "abc def ghi jkl mno " * 20}
            for i in range(50)
        ]

        result = _find_cache_alignment_point(msgs, config, system_tokens=100)
        assert result is not None
        assert 0 <= result < 50

        # Verify the result is better than a random index
        cumulative = 100  # system tokens
        for i in range(result + 1):
            cumulative += estimate_tokens([msgs[i]], config)
        best_remainder = min(
            cumulative % CACHE_BLOCK_SIZE,
            CACHE_BLOCK_SIZE - (cumulative % CACHE_BLOCK_SIZE),
        )

        # Check at a different index — best_remainder should be <= that one
        mid_idx = len(msgs) // 2
        mid_cumulative = 100
        for i in range(mid_idx + 1):
            mid_cumulative += estimate_tokens([msgs[i]], config)
        mid_remainder = min(
            mid_cumulative % CACHE_BLOCK_SIZE,
            CACHE_BLOCK_SIZE - (mid_cumulative % CACHE_BLOCK_SIZE),
        )

        # The found alignment point should be at least as good as midpoint
        assert best_remainder <= mid_remainder + 50  # tolerance for token rounding

    def test_returns_none_below_1024(self, config):
        """Alignment is skipped if total tokens < 1024."""
        msgs = [{"role": "user", "content": "x" * 100} for _ in range(2)]
        result = _find_cache_alignment_point(msgs, config, system_tokens=0)
        assert result is None

    def test_returns_none_for_empty_messages(self, config):
        """Empty message list with low system tokens returns None."""
        result = _find_cache_alignment_point([], config, system_tokens=500)
        assert result is None

    def test_with_system_tokens_near_boundary(self, config):
        """When system tokens are already near a boundary, early messages align."""
        # ~1016 system tokens — first message at ~50 tokens puts us near 1024
        msgs = [{"role": "user", "content": "x" * 200} for _ in range(5)]
        result = _find_cache_alignment_point(msgs, config, system_tokens=1016)
        assert result is not None
        # First message puts us at 1016 + 50 = 1066, remainder=42
        # That should be the best alignment
        assert result == 0

    def test_large_single_message(self, config):
        """A large message spanning multiple 1024 blocks — alignment after it."""
        msgs = [
            {"role": "user", "content": "x" * 200},   # ~50 tokens
            {"role": "tool", "content": "y" * 5000},  # ~1250 tokens
            {"role": "user", "content": "z" * 200},   # ~50 tokens
        ]
        result = _find_cache_alignment_point(msgs, config, system_tokens=500)
        assert result is not None
        # After the large message, cumulative ~1800 — should align near 2048


# ── Cache control injection tests ──────────────────────────────────────

class TestInjectCacheControl:
    def test_injects_into_string_content(self):
        """String content is wrapped in a list with cache_control on the block."""
        msg = {"role": "user", "content": "hello world"}
        result = _inject_cache_control(msg)
        assert isinstance(result["content"], list)
        assert result["content"][-1].get("cache_control") == {"type": "ephemeral"}
        assert result["content"][-1]["text"] == "hello world"

    def test_injects_into_content_list(self):
        """cache_control appended to last block in a content list."""
        msg = {"role": "assistant", "content": [
            {"type": "text", "text": "I'll do that."},
            {"type": "tool_use", "id": "t1", "name": "bash", "input": {}},
        ]}
        result = _inject_cache_control(msg)
        assert len(result["content"]) == 2
        assert result["content"][-1].get("cache_control") == {"type": "ephemeral"}
        # First block unchanged
        assert "cache_control" not in result["content"][0]

    def test_does_not_mutate_original(self):
        """Original message is not modified."""
        msg = {"role": "user", "content": "original"}
        _inject_cache_control(msg)
        assert msg["content"] == "original"


# ── Cache-aligned applySnip tests ──────────────────────────────────────

class TestCacheAlignedSnip:
    def test_standard_snip_without_system_tokens(self, config):
        """When system_tokens is None, standard snip behavior applies."""
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Task 1: the original question."},
        ] + [
            {"role": "user", "content": f"msg {i}"} for i in range(20)
        ]
        config.snip_keep_last = 4
        result = applySnip(msgs, config, system_tokens=None)
        # Standard snip: system + first user + last 4 = 6
        assert len(result) == 6
        assert result[0]["role"] == "system"

    def test_aligned_snip_finds_cache_boundary(self, config):
        """With system_tokens, snip aligns to 1024-token boundary."""
        # Build messages where we can predict alignment
        # Each message is ~50 tokens (200 chars / 4.0)
        msgs = [{"role": "user", "content": "task " + "x" * 195}]  # first task
        for i in range(25):
            msgs.append({"role": "user", "content": f"msg {i} " + "y" * 190})

        config.snip_keep_last = 4
        # System tokens near 1024 boundary so alignment shifts the cut
        result = applySnip(msgs, config, system_tokens=100)
        assert len(result) >= 2
        # Should have preserved first user message
        assert "task" in str(result[0].get("content", ""))
        # Check cache_control is injected at boundary
        has_cache = any(
            isinstance(m.get("content"), list)
            and any(
                isinstance(b, dict) and b.get("cache_control") == {"type": "ephemeral"}
                for b in m["content"]
            )
            for m in result
        )
        assert has_cache

    def test_noop_below_cache_threshold(self, config):
        """When total tokens < 1024, alignment no-ops and falls back to standard."""
        msgs = [
            {"role": "user", "content": "short"},
            {"role": "assistant", "content": "reply"},
        ]
        config.snip_keep_last = 4
        result = applySnip(msgs, config, system_tokens=100)
        # Too small to snip or align
        assert len(result) <= len(msgs)
        assert result == msgs  # Nothing to snip

    def test_no_system_messages_with_alignment(self, config):
        """Align works without system messages."""
        msgs = [{"role": "user", "content": f"msg {i} " + "x" * 195} for i in range(20)]
        config.snip_keep_last = 4
        result = applySnip(msgs, config, system_tokens=0)
        assert len(result) >= 2

    def test_aligned_snip_edge_system_tokens_zero(self, config):
        """With system_tokens=0, alignment still works from scratch."""
        msgs = [{"role": "user", "content": "x" * 200} for _ in range(30)]
        config.snip_keep_last = 4
        result = applySnip(msgs, config, system_tokens=0)
        assert len(result) >= 2


# ── Cache-aligned applyContextCollapse tests ───────────────────────────

class TestCacheAlignedContextCollapse:
    @pytest.mark.asyncio
    async def test_aligned_collapse_injects_cache_control(self, loop_config):
        """When system_tokens is provided, cache_control appears at recent boundary."""
        from d2c.compact import applyContextCollapse

        loop_config.compact_config.collapse_min_turns = 2
        loop_config.compact_config.collapse_segment_size = 3
        msgs = []
        for i in range(12):
            msgs.append({"role": "user", "content": f"Task {i} " + "a" * 180})
            msgs.append({"role": "assistant", "content": f"Response {i} " + "b" * 180})

        result = await applyContextCollapse(msgs, loop_config, system_tokens=500)
        assert isinstance(result, list)
        # Check for cache_control in recent messages
        has_cache = any(
            isinstance(m.get("content"), list)
            and any(
                isinstance(b, dict) and b.get("cache_control") == {"type": "ephemeral"}
                for b in m["content"]
            )
            for m in result
        )
        assert has_cache

    @pytest.mark.asyncio
    async def test_aligned_collapse_preserves_system(self, loop_config):
        """Aligned collapse still preserves system messages."""
        from d2c.compact import applyContextCollapse

        loop_config.compact_config.collapse_min_turns = 2
        loop_config.compact_config.collapse_segment_size = 3
        msgs = [
            {"role": "system", "content": "System prompt."},
            {"role": "user", "content": "Task 1 " + "x" * 180},
            {"role": "assistant", "content": "Response 1 " + "y" * 180},
            {"role": "user", "content": "Task 2 " + "x" * 180},
            {"role": "assistant", "content": "Response 2 " + "y" * 180},
            {"role": "user", "content": "Task 3 " + "x" * 180},
            {"role": "assistant", "content": "Response 3 " + "y" * 180},
            {"role": "user", "content": "Task 4 " + "x" * 180},
            {"role": "assistant", "content": "Response 4 " + "y" * 180},
        ]
        result = await applyContextCollapse(msgs, loop_config, system_tokens=100)
        assert result[0]["role"] == "system"
        assert "System prompt." in str(result[0].get("content", ""))

    @pytest.mark.asyncio
    async def test_noop_below_threshold_with_tokens(self, loop_config):
        """Below min_turns, collapse no-ops even with system_tokens."""
        from d2c.compact import applyContextCollapse

        loop_config.compact_config.collapse_min_turns = 10
        short = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello"},
        ]
        result = await applyContextCollapse(short, loop_config, system_tokens=500)
        assert result == short


# ── System+tools token computation tests ───────────────────────────────

class TestComputeSystemToolsTokens:
    def test_returns_positive_for_normal_input(self, config):
        """Computes a reasonable token estimate for system + tools."""
        system = "You are a helpful assistant."
        tools = [
            {"type": "function", "function": {"name": "Read", "parameters": {}}},
            {"type": "function", "function": {"name": "Bash", "parameters": {}}},
        ]
        tokens = _compute_system_tools_tokens(system, tools, config)
        assert tokens > 0

    def test_handles_empty_system_prompt(self, config):
        """Zero-length system prompt doesn't crash."""
        tools = [{"type": "function", "function": {"name": "Read"}}]
        tokens = _compute_system_tools_tokens("", tools, config)
        assert tokens >= 0

    def test_handles_empty_tools(self, config):
        """Empty tool list returns just framing overhead."""
        tokens = _compute_system_tools_tokens("System", [], config)
        # framing overhead only
        assert tokens >= 0

    def test_handles_both_empty(self, config):
        """Empty system and tools returns minimal framing."""
        tokens = _compute_system_tools_tokens("", [], config)
        assert tokens >= 0


# ── Integration: applyFullContextShapers with alignment ────────────────

class TestFullPipelineWithAlignment:
    @pytest.mark.asyncio
    async def test_pipeline_with_system_tokens(self, loop_config):
        """applyFullContextShapers passes system_tokens through to shapers."""
        from d2c.compact import applyFullContextShapers

        msgs = [
            {"role": "user", "content": "x" * 180}
            for _ in range(15)
        ]
        result = await applyFullContextShapers(msgs, loop_config, system_tokens=100)
        assert isinstance(result, list)
        # Pipeline should complete without error
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_pipeline_without_system_tokens(self, loop_config):
        """applyFullContextShapers works without system_tokens (backwards compat)."""
        from d2c.compact import applyFullContextShapers

        msgs = [
            {"role": "user", "content": f"msg {i}"}
            for i in range(5)
        ]
        result = await applyFullContextShapers(msgs, loop_config)
        assert isinstance(result, list)
        assert len(result) > 0
