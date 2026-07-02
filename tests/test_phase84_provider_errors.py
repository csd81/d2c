"""Phase 84: DeepSeek provider error UX.

Formatter unit tests use small duck-typed fake exceptions (status_code/message)
rather than the exact Anthropic SDK constructors, plus one loop integration to
prove streaming and non-streaming share the formatter.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from d2c.loop import TextResponse, queryLoop
from d2c.provider_errors import (
    ProviderErrorInfo,
    classify_provider_error,
    format_provider_error,
)
from tests.test_loop import make_loop_config


class FakeProviderError(Exception):
    """Duck-typed stand-in for an Anthropic APIStatusError."""

    def __init__(self, status_code=None, message=""):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class FakeConnectionError(Exception):
    """No HTTP status — looks like a network failure by class name."""


# ── status → message ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("status", "must_contain"),
    [
        (401, ["401", "DEEPSEEK_API_KEY"]),
        (402, ["402", "balance", "insufficient"]),
        (422, ["422", "model", "thinking", "max_tokens", "tool"]),
        (429, ["429", "rate-limiting", "Retry"]),
        (500, ["500", "server error", "Retry"]),
        (503, ["503", "unavailable", "Retry"]),
        (504, ["504", "timed out", "smaller"]),
    ],
)
def test_status_messages(status, must_contain):
    msg = format_provider_error(FakeProviderError(status, "provider detail"))
    for token in must_contain:
        assert token.lower() in msg.lower(), f"{token!r} missing from {msg!r}"


def test_402_is_distinct_from_auth_and_rate_limit():
    m402 = format_provider_error(FakeProviderError(402))
    m401 = format_provider_error(FakeProviderError(401))
    m429 = format_provider_error(FakeProviderError(429))
    assert m402 != m401 and m402 != m429
    assert "insufficient" in m402.lower()


def test_unknown_status_includes_code_and_detail():
    msg = format_provider_error(FakeProviderError(418, "I am a teapot"))
    assert "418" in msg
    assert "teapot" in msg.lower()


def test_connection_error_is_separate_from_http():
    info = classify_provider_error(FakeConnectionError("dns failure"))
    assert info.status_code is None
    assert info.kind == "connection"
    assert "reach deepseek" in info.message.lower()


def test_retryable_metadata():
    assert classify_provider_error(FakeProviderError(429)).retryable is True
    assert classify_provider_error(FakeProviderError(503)).retryable is True
    assert classify_provider_error(FakeProviderError(401)).retryable is False
    assert classify_provider_error(FakeProviderError(402)).retryable is False
    assert isinstance(classify_provider_error(FakeProviderError(500)), ProviderErrorInfo)


# ── no leakage ──────────────────────────────────────────────────────


def test_known_status_message_ignores_provider_detail():
    # For a known status the message is canned — a secret in the exception's
    # detail never reaches the output.
    secret = "prompt=SECRET_PROMPT tool_input=SECRET_TOOL"
    msg = format_provider_error(FakeProviderError(422, secret))
    assert "SECRET_PROMPT" not in msg
    assert "SECRET_TOOL" not in msg


def test_unknown_status_detail_is_redacted():
    msg = format_provider_error(
        FakeProviderError(418, "leaked DEEPSEEK_API_KEY=sk-abc123DEF456ghi")
    )
    assert "sk-abc123DEF456ghi" not in msg
    assert "[REDACTED]" in msg


# ── loop integration (shared formatter, both paths) ─────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_loop_uses_formatter_for_status_error(stream):
    lc = make_loop_config()
    lc.stream = stream
    err = FakeProviderError(402, "insufficient balance")

    with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
        client = MagicMock()
        if stream:
            client.messages.stream = MagicMock(side_effect=err)
        else:
            client.messages.create = AsyncMock(side_effect=err)
        mock_cls.return_value = client

        texts = [
            e.text
            async for e in queryLoop(lc, [{"role": "user", "content": "hi"}])
            if isinstance(e, TextResponse)
        ]

    assert texts
    assert "402" in texts[0]
    assert "balance" in texts[0].lower()
