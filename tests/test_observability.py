"""Phase 44: audit logging — redaction, event shape, correlation, privacy."""

import json

import pytest

import d2c.observability as obs
from d2c.observability import REDACTED, AuditLogger, audit, redact, set_audit_logger


@pytest.fixture(autouse=True)
def _reset_logger():
    yield
    set_audit_logger(None)


def _logger(tmp_dir, **kw):
    path = tmp_dir / "audit.jsonl"
    lg = AuditLogger(path=path, enabled=True, **kw)
    set_audit_logger(lg)
    return lg, path


def _read(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ── Redaction ─────────────────────────────────────────────────────────


def test_redacts_deepseek_style_key():
    assert redact("token is sk-abcdef123456 ok") == "token is [REDACTED] ok"


def test_redacts_tavily_style_key():
    assert redact("key tvly-abcdef123456 here") == "key [REDACTED] here"


def test_redacts_by_field_name():
    out = redact({"Authorization": "Bearer xyz", "x-api-key": "whatever", "safe": "keep"})
    assert out["Authorization"] == REDACTED
    assert out["x-api-key"] == REDACTED
    assert out["safe"] == "keep"


def test_redacts_nested_and_lists():
    out = redact({"a": [{"password": "hunter2"}, "sk-deadbeef1234"]})
    assert out["a"][0]["password"] == REDACTED
    assert out["a"][1] == REDACTED


def test_redacts_literal_env_secret(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "weird-nonprefixed-secret-value")
    assert redact("uses weird-nonprefixed-secret-value inline") == "uses [REDACTED] inline"


def test_truncates_long_strings():
    out = redact("x" * 900)
    assert out.endswith("... [truncated]")
    assert len(out) < 600


# ── Event emission + shape ────────────────────────────────────────────


def test_emits_jsonl_with_required_fields(tmp_dir):
    lg, path = _logger(tmp_dir)
    lg.set_context(session_id="s1", model="deepseek-v4-pro")
    audit("tool_call_end", tool_name="Read", tool_call_id="tc1", duration_ms=3, error=False)
    (rec,) = _read(path)
    for field in ("ts", "level", "event", "session_id", "tool_name", "tool_call_id"):
        assert field in rec
    assert rec["event"] == "tool_call_end"
    assert rec["session_id"] == "s1"


def test_correlation_shares_tool_call_id(tmp_dir):
    lg, path = _logger(tmp_dir)
    audit("tool_call_start", tool_name="Bash", tool_call_id="tcX")
    audit("tool_call_end", tool_name="Bash", tool_call_id="tcX", error=False)
    recs = _read(path)
    assert {r["event"] for r in recs} == {"tool_call_start", "tool_call_end"}
    assert all(r["tool_call_id"] == "tcX" for r in recs)


def test_permission_event_has_decision_and_no_secret(tmp_dir, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-supersecretkey123")
    lg, path = _logger(tmp_dir)
    audit(
        "permission_denied", tool_name="Bash", reason="acceptEdits denied; key sk-supersecretkey123"
    )
    (rec,) = _read(path)
    assert rec["event"] == "permission_denied"
    assert "sk-supersecretkey123" not in json.dumps(rec)
    assert REDACTED in rec["reason"]


def test_websearch_event_omits_api_key(tmp_dir):
    lg, path = _logger(tmp_dir)
    audit("websearch_error", provider="tavily", error_class="WebSearchAuthError")
    (rec,) = _read(path)
    assert rec["provider"] == "tavily"
    assert "tvly" not in json.dumps(rec)


# ── Privacy defaults + disabled behavior ──────────────────────────────


def test_disabled_logger_writes_nothing(tmp_dir):
    path = tmp_dir / "audit.jsonl"
    lg = AuditLogger(path=path, enabled=False)
    set_audit_logger(lg)
    audit("tool_call_start", tool_name="Read")
    assert not path.exists()


def test_no_logger_is_noop():
    set_audit_logger(None)
    audit("anything", foo="bar")  # must not raise


def test_tool_output_and_prompts_off_by_default(tmp_dir):
    lg, _ = _logger(tmp_dir)
    assert obs.logs_tool_outputs() is False
    assert obs.logs_prompts() is False


def test_level_filtering(tmp_dir):
    path = tmp_dir / "audit.jsonl"
    lg = AuditLogger(path=path, enabled=True, level="WARNING")
    set_audit_logger(lg)
    audit("debug_event", level="INFO")  # below threshold → dropped
    audit("warn_event", level="WARNING")  # kept
    recs = _read(path)
    assert [r["event"] for r in recs] == ["warn_event"]
