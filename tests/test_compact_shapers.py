"""Tests for Phase 12: 3 new compaction shapers (Snip, Microcompact, Context Collapse).

Covers all 5 shapers in the pipeline plus edge cases.
"""

from __future__ import annotations

import pytest

from d2c.compact import (
    CompactConfig,
    applyBudgetReduction,
    applySnip,
    applyMicrocompact,
    applyContextCollapse,
    applyContextShapers,
    applyFullContextShapers,
    checkPressure,
    estimate_tokens,
    _has_tool_use,
    _get_tool_names,
    _content_str,
    _segment_messages,
    _summarize_segment,
)


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def config():
    return CompactConfig(
        tool_result_max_chars=30_000,
        pressure_threshold=0.85,
        context_window_tokens=128_000,
        snip_keep_last=6,
        microcompact_summary_max_chars=500,
        collapse_min_turns=3,
        collapse_segment_size=4,
    )


@pytest.fixture
def sample_messages():
    """A realistic message sequence with system, user, tool calls, and tool results."""
    return [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Please help me write a function."},
        {"role": "assistant", "content": "Sure, let me create that."},
        {"role": "user", "content": "Add error handling too."},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "write", "input": {"path": "foo.py"}},
        ]},
        {"role": "tool", "content": "Wrote file foo.py successfully."},
        {"role": "user", "content": "Now add tests."},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t2", "name": "write", "input": {"path": "test_foo.py"}},
        ]},
        {"role": "tool", "content": "Wrote file test_foo.py successfully."},
        {"role": "user", "content": "Great, now add type hints."},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t3", "name": "edit", "input": {"path": "foo.py"}},
        ]},
        {"role": "tool", "content": "Updated foo.py with type hints."},
    ]


# ── Shaper 2: Snip ────────────────────────────────────────────────────────

class TestSnip:
    def test_trims_oldest_non_system_messages(self, config, sample_messages):
        """Snip removes oldest messages, keeping system + task + recent."""
        config.snip_keep_last = 4
        result = applySnip(sample_messages, config)
        # Keep: system (1) + first user task (1) + last 4 = 6
        # Original has 12 messages, so we trimmed 6
        assert len(result) == 6
        assert len(result) < len(sample_messages)
        # System message preserved
        assert result[0]["role"] == "system"

    def test_preserves_first_user_message(self, config, sample_messages):
        """The first user message (the task) is preserved."""
        config.snip_keep_last = 3
        result = applySnip(sample_messages, config)
        # First user message "Please help me write a function." should be kept
        user_contents = [m["content"] for m in result if m["role"] == "user"]
        assert any("Please help me write a function" in c for c in user_contents)

    def test_preserves_last_n(self, config, sample_messages):
        """Last N messages are always preserved."""
        config.snip_keep_last = 3
        result = applySnip(sample_messages, config)
        # Last 3 of original should be in result
        last_three_orig = sample_messages[-3:]
        for msg in last_three_orig:
            assert msg in result

    def test_noop_on_short_conversation(self, config):
        """Snip does nothing when messages <= keep_last."""
        config.snip_keep_last = 20
        short = [
            {"role": "system", "content": "System."},
            {"role": "user", "content": "Hello."},
            {"role": "assistant", "content": "Hi!"},
        ]
        result = applySnip(short, config)
        assert result == short

    def test_no_system_messages(self, config):
        """Snip works without system messages."""
        config.snip_keep_last = 2
        msgs = [
            {"role": "user", "content": "Task 1"},
            {"role": "assistant", "content": "Response 1"},
            {"role": "user", "content": "Task 2"},
            {"role": "assistant", "content": "Response 2"},
            {"role": "user", "content": "Task 3"},
            {"role": "assistant", "content": "Response 3"},
        ]
        result = applySnip(msgs, config)
        # Should have first user + last 2 = 3
        assert len(result) == 3
        assert result[0]["content"] == "Task 1"
        assert result[-1]["content"] == "Response 3"


# ── Shaper 3: Microcompact ────────────────────────────────────────────────

class TestMicrocompact:
    def test_summarizes_tool_result_pairs(self, config, sample_messages):
        """Microcompact collapses tool use/result pairs into summaries."""
        result = applyMicrocompact(sample_messages, config)
        # Should have fewer messages due to compaction
        assert len(result) < len(sample_messages)
        # Should contain a microcompact summary
        summaries = [m for m in result if "Microcompact" in str(m.get("content", ""))]
        assert len(summaries) >= 1

    def test_noop_on_few_messages(self, config):
        """Microcompact does nothing with < 4 messages."""
        short = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ]
        result = applyMicrocompact(short, config)
        assert result == short

    def test_preserves_system_messages(self, config, sample_messages):
        """Microcompact preserves system messages at boundaries."""
        result = applyMicrocompact(sample_messages, config)
        assert result[0]["role"] == "system"

    def test_respects_cache_boundaries(self, config):
        """Microcompact respects cache-safe boundaries (non-tool user messages)."""
        msgs = [
            {"role": "user", "content": "Plain message, no tools."},
            {"role": "assistant", "content": "Plain response."},
            {"role": "user", "content": "Now use a tool."},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "bash", "input": {}},
            ]},
            {"role": "tool", "content": "Command output"},
        ]
        result = applyMicrocompact(msgs, config)
        # First plain exchange should be preserved (cache boundary)
        assert result[0]["content"] == "Plain message, no tools."
        assert result[1]["content"] == "Plain response."

    def test_multiple_tool_pairs(self, config):
        """Microcompact handles consecutive tool-use pairs."""
        msgs = [
            {"role": "user", "content": "Task 1"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "tool_a", "input": {}},
            ]},
            {"role": "tool", "content": "Result A"},
            {"role": "user", "content": "Task 2"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t2", "name": "tool_b", "input": {}},
            ]},
            {"role": "tool", "content": "Result B"},
            {"role": "user", "content": "Task 3"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t3", "name": "tool_c", "input": {}},
            ]},
            {"role": "tool", "content": "Result C"},
        ]
        result = applyMicrocompact(msgs, config)
        # Should have compacted 3 consecutive pairs
        summaries = [m for m in result if "Microcompact" in str(m.get("content", ""))]
        assert len(summaries) >= 1


# ── Shaper 4: Context Collapse ─────────────────────────────────────────────

class TestContextCollapse:
    def test_produces_read_time_projection(self, config):
        """Context collapse creates a read-time view without modifying original."""
        msgs = [
            {"role": "user", "content": "Task 1"},
            {"role": "assistant", "content": "Response 1"},
            {"role": "user", "content": "Task 2"},
            {"role": "assistant", "content": "Response 2"},
            {"role": "user", "content": "Task 3"},
            {"role": "assistant", "content": "Response 3"},
            {"role": "user", "content": "Task 4"},
            {"role": "assistant", "content": "Response 4"},
            {"role": "user", "content": "Task 5"},
            {"role": "assistant", "content": "Response 5"},
        ]
        config.collapse_min_turns = 2
        config.collapse_segment_size = 3
        result = applyContextCollapse(msgs, config)
        # Original unchanged
        assert len(msgs) == 10
        # Result is a projection
        assert isinstance(result, list)
        # Contains segment summaries
        has_segment = any("Context segment" in str(m.get("content", "")) for m in result)
        assert has_segment

    def test_preserves_system_and_first_user(self, config):
        """Collapse preserves system messages and first user message."""
        msgs = [
            {"role": "system", "content": "System prompt here."},
            {"role": "user", "content": "Original task."},
            {"role": "assistant", "content": "Response 1"},
            {"role": "user", "content": "Task 2"},
            {"role": "assistant", "content": "Response 2"},
            {"role": "user", "content": "Task 3"},
            {"role": "assistant", "content": "Response 3"},
            {"role": "user", "content": "Task 4"},
            {"role": "assistant", "content": "Response 4"},
        ]
        config.collapse_min_turns = 2
        config.collapse_segment_size = 3
        result = applyContextCollapse(msgs, config)
        # System message at top
        assert result[0]["content"] == "System prompt here."
        # First user message preserved
        assert any("Original task." in str(m.get("content", "")) for m in result)

    def test_preserves_recent_messages(self, config):
        """Recent messages at the end are preserved uncollapsed."""
        msgs = [
            {"role": "user", "content": "Task 1"},
            {"role": "assistant", "content": "Response 1"},
            {"role": "user", "content": "Task 2"},
            {"role": "assistant", "content": "Response 2"},
            {"role": "user", "content": "Task 3"},
            {"role": "assistant", "content": "Response 3"},
            {"role": "user", "content": "Task 4"},
            {"role": "assistant", "content": "Response 4"},
            {"role": "user", "content": "Final task."},
            {"role": "assistant", "content": "Final response."},
        ]
        config.collapse_min_turns = 2
        config.collapse_segment_size = 4
        result = applyContextCollapse(msgs, config)
        # Recent messages preserved
        assert any("Final task." in str(m.get("content", "")) for m in result)
        assert any("Final response." in str(m.get("content", "")) for m in result)

    def test_noop_on_few_turns(self, config):
        """Context collapse skips when below min_turns."""
        config.collapse_min_turns = 5
        short = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ]
        result = applyContextCollapse(short, config)
        assert result == short


# ── Full Pipeline ──────────────────────────────────────────────────────────

class TestPipeline:
    def test_apply_context_shapers_budget_reduction(self, config):
        """applyContextShapers always applies budget reduction."""
        long_content = "x" * 50_000
        msgs = [
            {"role": "tool", "content": long_content},
        ]
        result = applyContextShapers(msgs, config)
        assert len(result[0]["content"]) < len(long_content)
        assert "[truncated" in result[0]["content"]

    def test_pipeline_order_preserves_short_messages(self, config):
        """Messages under pressure threshold are unchanged by gated shapers."""
        short = [
            {"role": "system", "content": "Sys"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ]
        result = applyContextShapers(short, config)
        # Budget reduction shouldn't truncate short messages
        assert result == short


# ── Helpers ─────────────────────────────────────────────────────────────────

class TestHelpers:
    def test_has_tool_use_with_list_content(self):
        msg = {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "bash", "input": {}},
        ]}
        assert _has_tool_use(msg) is True

    def test_has_tool_use_with_string_content(self):
        msg = {"role": "assistant", "content": "No tools here."}
        assert _has_tool_use(msg) is False

    def test_has_tool_use_without_tool_blocks(self):
        msg = {"role": "assistant", "content": [
            {"type": "text", "text": "Just text."},
        ]}
        assert _has_tool_use(msg) is False

    def test_get_tool_names(self):
        msg = {"content": [
            {"type": "tool_use", "id": "t1", "name": "write"},
            {"type": "tool_use", "id": "t2", "name": "bash"},
        ]}
        names = _get_tool_names(msg)
        assert names == ["write", "bash"]

    def test_get_tool_names_empty(self):
        msg = {"content": "string content"}
        assert _get_tool_names(msg) == []

    def test_content_str_string(self):
        assert _content_str({"content": "hello"}) == "hello"

    def test_content_str_list(self):
        msg = {"content": [
            {"type": "text", "text": "Hello"},
            {"type": "tool_use", "id": "t1", "name": "bash"},
        ]}
        result = _content_str(msg)
        assert "Hello" in result
        assert "tool_use" in result

    def test_segment_messages(self):
        msgs = [{"role": "user", "content": str(i)} for i in range(10)]
        segments = _segment_messages(msgs, 3)
        assert len(segments) == 4  # 3 + 3 + 3 + 1
        assert len(segments[0]) == 3
        assert len(segments[-1]) == 1

    def test_segment_messages_empty(self):
        assert _segment_messages([], 3) == []

    def test_summarize_segment(self):
        segment = [
            {"role": "user", "content": "Do something"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "bash"},
            ]},
        ]
        summary = _summarize_segment(segment, 0)
        assert "Context segment 1" in summary
        assert "2 messages" in summary
        assert "bash" in summary


# ── Edge Cases ─────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_snip_empty_messages(self, config):
        result = applySnip([], config)
        assert result == []

    def test_microcompact_empty_messages(self, config):
        result = applyMicrocompact([], config)
        assert result == []

    def test_context_collapse_empty_messages(self, config):
        result = applyContextCollapse([], config)
        assert result == []

    def test_snip_keep_last_greater_than_messages(self, config):
        """When keep_last > total messages, all messages preserved."""
        config.snip_keep_last = 100
        msgs = [{"role": "user", "content": "Hi"}]
        result = applySnip(msgs, config)
        assert result == msgs

    def test_pipeline_with_none_config(self):
        """applyContextShapers returns messages unchanged when config is None."""
        msgs = [{"role": "user", "content": "Hello"}]
        result = applyContextShapers(msgs, None)
        assert result == msgs
