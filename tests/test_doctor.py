"""Phase 47: config doctor / diagnostics."""

import json
from types import SimpleNamespace

from d2c.doctor import (
    DoctorResult,
    check_audit,
    check_deepseek,
    check_python,
    check_skills_plugins,
    check_trust,
    check_websearch,
    exit_code,
    render_json,
    render_text,
    run_doctor,
    summarize,
)


def _cfg(**kw):
    base = dict(
        model="deepseek-v4-pro",
        deepseek_api_key="sk-x",
        websearch_provider="",
        websearch_api_key=None,
        sandbox_enabled=False,
        audit_log_enabled=False,
        audit_log_path="",
        log_prompts=False,
        log_tool_outputs=False,
    )
    base.update(kw)
    return SimpleNamespace(**base)


# ── Result model / rendering ──────────────────────────────────────────


def test_summary_counts():
    rs = [
        DoctorResult("a", "pass", ""),
        DoctorResult("b", "warn", ""),
        DoctorResult("c", "fail", ""),
        DoctorResult("d", "pass", ""),
    ]
    assert summarize(rs) == {"pass": 2, "warn": 1, "fail": 1}
    assert exit_code(rs) == 1
    assert exit_code([DoctorResult("x", "warn", "")]) == 0  # warnings don't fail


def test_text_renderer():
    out = render_text(
        [
            DoctorResult("Python", "pass", "3.13"),
            DoctorResult("Trust", "warn", "untrusted", fix="use --trust"),
        ]
    )
    assert "PASS Python" in out
    assert "WARN Trust" in out
    assert "fix: use --trust" in out
    assert "Summary: 1 passed, 1 warnings, 0 failed" in out


def test_json_renderer_is_machine_readable():
    out = render_json([DoctorResult("Python", "pass", "3.13")])
    data = json.loads(out)
    assert data["summary"] == {"pass": 1, "warn": 0, "fail": 0}
    assert data["results"][0]["name"] == "Python"


# ── Individual checks ─────────────────────────────────────────────────


def test_python_check_passes_on_supported():
    assert check_python().status == "pass"


def test_missing_deepseek_key_warns_without_leak():
    r = check_deepseek(_cfg(deepseek_api_key=None))
    assert r.status == "warn"
    assert "sk-" not in r.message  # never echoes a key value


def test_unsupported_websearch_provider_fails():
    r = check_websearch(_cfg(websearch_provider="bing", websearch_api_key="x"))
    assert r.status == "fail"
    assert "unsupported" in r.message.lower()


def test_unconfigured_websearch_warns():
    assert check_websearch(_cfg()).status == "warn"


def test_configured_websearch_passes_without_leaking_key():
    r = check_websearch(_cfg(websearch_provider="tavily", websearch_api_key="tvly-secret"))
    assert r.status == "pass"
    assert "tvly-secret" not in r.message


def test_audit_enabled_unwritable_path_fails(tmp_dir):
    afile = tmp_dir / "afile"
    afile.write_text("x")  # a regular file used as a parent → cannot mkdir under it
    r = check_audit(_cfg(audit_log_enabled=True, audit_log_path=str(afile / "audit.jsonl")))
    assert r.status == "fail"


def test_audit_full_logging_warns(tmp_dir):
    r = check_audit(
        _cfg(audit_log_enabled=True, audit_log_path=str(tmp_dir / "a.jsonl"), log_prompts=True)
    )
    assert r.status == "warn"
    assert "prompts" in r.message


def test_bundled_skill_missing_fails(tmp_dir, monkeypatch, trusted_gate):
    monkeypatch.setattr("d2c.skills.loader.load_bundled_skills", lambda: [])
    r = check_skills_plugins(tmp_dir, trusted=True)
    assert r.status == "fail"


def test_untrusted_workspace_reports_skips(tmp_dir):
    assert check_trust(False).status == "warn"
    # local skills present but skipped under untrusted
    (tmp_dir / ".d2c" / "skills").mkdir(parents=True)
    r = check_skills_plugins(tmp_dir, trusted=False)
    assert r.status == "warn"
    assert "skipped" in r.message.lower()


# ── Orchestration + CLI ───────────────────────────────────────────────


def test_run_doctor_offline_produces_all_checks(tmp_dir, trusted_gate):
    results = run_doctor(_cfg(), cwd=tmp_dir, trusted=True, live=False)
    names = {r.name for r in results}
    assert {
        "Python",
        "Imports",
        "DeepSeek",
        "WebSearch",
        "Git",
        "Workspace",
        "Trust",
        "Sandbox",
        "Audit log",
        "MCP",
        "Skills",
    } <= names
    # offline run never adds the live probe
    assert "WebSearch (live)" not in names


def test_doctor_cli_exits_before_agent_loop(tmp_dir, monkeypatch):
    # main() with --doctor must go through _run_doctor_cli and never start the loop.
    import d2c.main as m

    called = {"headless": False, "interactive": False}
    monkeypatch.setattr(m, "run_headless", lambda *a, **k: called.__setitem__("headless", True))
    monkeypatch.setattr(
        m, "run_interactive", lambda *a, **k: called.__setitem__("interactive", True)
    )

    args = SimpleNamespace(cwd=tmp_dir, no_trust=True, trust=False, doctor_live=False, json=False)
    code = m._run_doctor_cli(args)
    assert code in (0, 1)
    assert called == {"headless": False, "interactive": False}
