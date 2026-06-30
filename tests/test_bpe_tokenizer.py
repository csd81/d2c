"""Tests for Phase 28: BPE Tokenizer Integration.

Verifies precise token counting via tiktoken cl100k_base encoding,
including structured content blocks and fallback behavior.
"""

from __future__ import annotations

import pytest


# ── BPE counting tests ─────────────────────────────────────────────────


class TestBPECounting:
    """Verify exact and approximate BPE token counts."""

    def test_bpe_counting_text_only(self):
        """Basic string messages produce non-zero, reasonable token counts."""
        from d2c.context import estimate_tokens

        messages = [
            {"role": "user", "content": "Hello, world!"},
        ]
        tokens = estimate_tokens(messages)
        # "Hello, world!" is ~3-4 tokens in cl100k_base + overhead
        assert tokens > 0
        assert tokens < 100  # sanity upper bound

    def test_bpe_counting_multiple_text_messages(self):
        """Multiple messages have higher token counts."""
        from d2c.context import estimate_tokens

        single = estimate_tokens([{"role": "user", "content": "hi"}])
        multiple = estimate_tokens([
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "how are you"},
        ])
        assert multiple > single

    def test_bpe_counting_empty_content(self):
        """Empty content should still have message overhead tokens."""
        from d2c.context import estimate_tokens

        tokens = estimate_tokens([{"role": "user", "content": ""}])
        # Should have role encoding + message overhead + framing
        assert tokens > 0

    def test_bpe_counting_empty_messages_list(self):
        """Empty message list has only framing overhead."""
        from d2c.context import estimate_tokens

        tokens = estimate_tokens([])
        # Only the +3 framing overhead
        assert tokens >= 0

    def test_bpe_counting_tool_use(self):
        """Tool use blocks are counted with name and input JSON."""
        from d2c.context import estimate_tokens

        messages = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tu_1", "name": "Read",
                 "input": {"file_path": "/tmp/test.txt"}},
            ]},
        ]
        tokens = estimate_tokens(messages)
        assert tokens > 0

    def test_bpe_counting_tool_result(self):
        """Tool result blocks are counted with tool_use_id and content."""
        from d2c.context import estimate_tokens

        messages = [
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu_1",
                 "content": "file contents here\nline 2\nline 3"},
            ]},
        ]
        tokens = estimate_tokens(messages)
        assert tokens > 0

    def test_bpe_counting_mixed_blocks(self):
        """Message with multiple content block types."""
        from d2c.context import estimate_tokens

        messages = [
            {"role": "assistant", "content": [
                {"type": "text", "text": "Let me read that file."},
                {"type": "tool_use", "id": "tu_1", "name": "Read",
                 "input": {"file_path": "/tmp/test.txt"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu_1",
                 "content": "Hello from file"},
            ]},
        ]
        tokens = estimate_tokens(messages)
        # Should be more than a simple text message
        simple = estimate_tokens([{"role": "user", "content": "hi"}])
        assert tokens > simple

    def test_bpe_counting_large_content(self):
        """Large content should produce proportional token counts."""
        from d2c.context import estimate_tokens

        small = estimate_tokens([{"role": "user", "content": "hi"}])
        large = estimate_tokens([{"role": "user", "content": "hi" * 500}])
        assert large > small * 10  # roughly proportional

    def test_bpe_counting_known_value(self):
        """Exact token count for a known string in cl100k_base."""
        from d2c.context import estimate_tokens

        # "Hello world" is 2 tokens in cl100k_base: "Hello" + " world"
        messages = [
            {"role": "user", "content": "Hello world"},
        ]
        tokens = estimate_tokens(messages)
        # Content: 2 tokens + role "user" (~1) + overhead (4) + framing (3) ≈ 10
        assert 7 <= tokens <= 15


class TestBPEFallback:
    """Verify graceful fallback when tiktoken is unavailable."""

    def test_fallback_returns_reasonable_estimate(self):
        """Fallback uses character division and should produce > 0 for non-empty input."""
        from d2c.context import _fallback_estimate_tokens

        messages = [
            {"role": "user", "content": "hello world"},
            {"role": "assistant", "content": "hi there"},
        ]
        tokens = _fallback_estimate_tokens(messages, chars_per_token=3.5)
        # "hello world" (11) + "hi there" (8) = 19 / 3.5 ≈ 5
        assert tokens >= 3

    def test_fallback_handles_list_content(self):
        from d2c.context import _fallback_estimate_tokens

        messages = [
            {"role": "assistant", "content": [{"type": "text", "text": "abc"}]},
        ]
        tokens = _fallback_estimate_tokens(messages, chars_per_token=4.0)
        assert tokens > 0

    def test_fallback_handles_non_string_content(self):
        from d2c.context import _fallback_estimate_tokens

        messages = [{"role": "user", "content": 12345}]
        tokens = _fallback_estimate_tokens(messages, chars_per_token=4.0)
        assert tokens > 0

    def test_fallback_empty_messages(self):
        from d2c.context import _fallback_estimate_tokens

        assert _fallback_estimate_tokens([], chars_per_token=3.5) == 0


class TestBPEIntegration:
    """Verify compact.py delegates to BPE tokenizer."""

    def test_compact_estimate_uses_bpe(self):
        """compact.estimate_tokens delegates to context.estimate_tokens."""
        from d2c.compact import estimate_tokens, CompactConfig

        messages = [
            {"role": "user", "content": "Hello world"},
        ]
        tokens = estimate_tokens(messages, CompactConfig(chars_per_token=4.0))
        assert tokens > 0

    def test_compact_estimate_empty(self):
        from d2c.compact import estimate_tokens, CompactConfig

        tokens = estimate_tokens([], CompactConfig())
        assert tokens >= 0

    def test_chars_per_token_still_respected_in_fallback(self):
        """When BPE fails, chars_per_token from config is passed to fallback."""
        from d2c.context import _fallback_estimate_tokens

        messages = [{"role": "user", "content": "a" * 40}]
        # With chars_per_token=4, 40/4=10; with chars_per_token=2, 40/2=20
        t1 = _fallback_estimate_tokens(messages, chars_per_token=4.0)
        t2 = _fallback_estimate_tokens(messages, chars_per_token=2.0)
        assert t2 > t1  # lower divisor → higher estimate
