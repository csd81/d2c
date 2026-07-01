"""Phase 55: cost and token accounting."""

import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from d2c.observability import AuditLogger, set_audit_logger
from d2c.usage import (
    ModelUsage,
    SessionUsage,
    UsageTracker,
    compute_cost,
    extract_usage,
    format_session_usage,
    get_usage_tracker,
    record_model_usage,
    set_usage_tracker,
    usage_status_fragment,
)


def _response(input_tokens=1000, output_tokens=200, cache_read=0, cache_write=0):
    return SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_write,
        )
    )


@pytest.fixture(autouse=True)
def _clear_pricing_env(monkeypatch):
    for var in (
        "D2C_PRICING_INPUT_PER_MILLION",
        "D2C_PRICING_OUTPUT_PER_MILLION",
        "D2C_PRICING_CACHE_READ_PER_MILLION",
        "D2C_DISABLE_COST_ESTIMATES",
    ):
        monkeypatch.delenv(var, raising=False)
    yield
    set_audit_logger(None)


# ── Extraction ────────────────────────────────────────────────────────


def test_extracts_anthropic_style_usage_fields():
    mu = extract_usage(
        _response(input_tokens=1234, output_tokens=56, cache_read=1000, cache_write=200),
        model="deepseek-chat",
    )
    assert mu.input_tokens == 1234
    assert mu.output_tokens == 56
    assert mu.cache_read_tokens == 1000
    assert mu.cache_write_tokens == 200
    assert mu.estimated is False


def test_fallback_estimation_when_usage_absent():
    messages = [{"role": "user", "content": "hello " * 100}]
    mu = extract_usage(None, model="deepseek-chat", fallback_messages=messages, fallback_text="hi")
    assert mu.estimated is True
    assert mu.input_tokens > 0
    assert mu.output_tokens > 0


def test_extraction_never_raises_on_weird_response():
    class Weird:
        usage = object()  # attributes missing entirely

    mu = extract_usage(Weird(), model="deepseek-chat", fallback_text="x")
    assert mu.estimated is True  # missing fields -> estimation path


# ── Cost ──────────────────────────────────────────────────────────────


def test_cost_calculation_uses_decimal():
    # deepseek-chat: in 0.56/M, out 1.68/M, cache read 0.07/M, write 0.56/M
    cost, known = compute_cost(
        "deepseek-chat",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_read_tokens=1_000_000,
        cache_write_tokens=1_000_000,
    )
    assert known is True
    assert isinstance(cost, Decimal)
    assert cost == Decimal("0.56") + Decimal("1.68") + Decimal("0.07") + Decimal("0.56")


def test_unknown_model_tracks_tokens_but_no_cost():
    mu = extract_usage(_response(500, 100), model="totally-unknown-model")
    assert mu.input_tokens == 500 and mu.output_tokens == 100
    assert mu.cost_known is False
    assert mu.estimated_cost_usd == Decimal("0")


def test_pricing_env_overrides(monkeypatch):
    monkeypatch.setenv("D2C_PRICING_INPUT_PER_MILLION", "2.00")
    monkeypatch.setenv("D2C_PRICING_OUTPUT_PER_MILLION", "10.00")
    cost, known = compute_cost("deepseek-chat", 1_000_000, 500_000)
    assert known is True
    assert cost == Decimal("2.00") + Decimal("5.00")
    # Overrides alone also price an unknown model.
    cost2, known2 = compute_cost("totally-unknown-model", 1_000_000, 0)
    assert known2 is True and cost2 == Decimal("2.00")


def test_disable_cost_estimates(monkeypatch):
    monkeypatch.setenv("D2C_DISABLE_COST_ESTIMATES", "1")
    cost, known = compute_cost("deepseek-chat", 1_000_000, 1_000_000)
    assert known is False and cost == Decimal("0")
    mu = extract_usage(_response(), model="deepseek-chat")
    assert mu.input_tokens == 1000  # tokens still tracked
    assert "disabled" in format_session_usage(SessionUsage())


# ── Session totals ────────────────────────────────────────────────────


def test_session_totals_accumulate():
    t = UsageTracker()
    t.record(extract_usage(_response(1000, 100), model="deepseek-chat"))
    t.record(extract_usage(_response(2000, 200, cache_read=500), model="deepseek-chat"))
    s = t.session
    assert s.calls == 2
    assert s.input_tokens == 3000
    assert s.output_tokens == 300
    assert s.cache_read_tokens == 500
    assert s.estimated_cost_usd > Decimal("0")
    t.reset()
    assert t.session.calls == 0


def test_record_model_usage_feeds_active_tracker():
    t = UsageTracker()
    set_usage_tracker(t)
    assert get_usage_tracker() is t
    record_model_usage("deepseek-chat", _response(100, 10), turn_id=0)
    record_model_usage("deepseek-chat", _response(100, 10), turn_id=0)  # recovery retry
    # Each model call (including output-token recovery retries) counts.
    assert t.session.calls == 2
    assert t.session.input_tokens == 200


def test_record_model_usage_never_raises_without_tracker():
    assert get_usage_tracker() is None
    mu = record_model_usage("deepseek-chat", _response(1, 1))
    assert mu is not None and mu.input_tokens == 1


# ── Loop integration: usage recorded per model call ───────────────────


@pytest.mark.asyncio
async def test_loop_records_usage_per_model_call():
    from d2c.loop import queryLoop
    from tests.test_loop import make_loop_config, make_text_response

    tracker = UsageTracker()
    set_usage_tracker(tracker)

    response = make_text_response("done")
    response.usage = SimpleNamespace(
        input_tokens=4321,
        output_tokens=87,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )

    with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=response)
        mock_cls.return_value = mock_client
        async for _ in queryLoop(make_loop_config(), [{"role": "user", "content": "hi"}]):
            pass

    assert tracker.session.calls == 1
    assert tracker.session.input_tokens == 4321
    assert tracker.session.output_tokens == 87
    assert tracker.session.any_estimated is False


# ── Audit events ──────────────────────────────────────────────────────


def test_model_usage_audit_has_tokens_but_no_prompt(tmp_dir):
    path = tmp_dir / "audit.jsonl"
    set_audit_logger(AuditLogger(path=path, enabled=True))
    secret_prompt = "please refactor my SUPER-SECRET-PROJECT-NAME module"
    record_model_usage(
        "deepseek-chat",
        None,
        fallback_messages=[{"role": "user", "content": secret_prompt}],
        fallback_text="ok",
        turn_id=3,
    )
    lines = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    events = {e["event"] for e in lines}
    assert "model_usage" in events
    rec = next(e for e in lines if e["event"] == "model_usage")
    assert rec["input_tokens"] > 0 and rec["turn_id"] == 3 and rec["estimated"] is True
    assert "SUPER-SECRET-PROJECT-NAME" not in path.read_text()


# ── Formatting ────────────────────────────────────────────────────────


def test_format_session_usage_readable():
    s = SessionUsage(
        calls=8,
        input_tokens=124_302,
        output_tokens=9_184,
        cache_read_tokens=64_000,
        cache_write_tokens=8_192,
        estimated_cost_usd=Decimal("0.42"),
    )
    out = format_session_usage(s, session_id="abc123")
    assert "abc123" in out
    assert "Model calls:   8" in out
    assert "124,302" in out and "9,184" in out
    assert "~$0.42" in out and "estimate" in out


def test_status_fragment_compact():
    s = SessionUsage(
        calls=2, input_tokens=133_400, output_tokens=9_200, estimated_cost_usd=Decimal("0.42")
    )
    frag = usage_status_fragment(s)
    assert "133.4k in" in frag and "9.2k out" in frag and "~$0.42" in frag


def test_unknown_cost_marked_in_format():
    s = SessionUsage(calls=1, input_tokens=10, cost_known=False)
    assert "unknown" in format_session_usage(s)
    m = ModelUsage(model="x")
    assert m.estimated_cost_usd == Decimal("0")
