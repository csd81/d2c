"""Phase 74: Textual TUI foundation.

Tests the Textual-free helpers (command reuse, approval mapping, Markdown
fallback, status line, gating) and the non-interactive boundary. The Textual
app itself is only exercised where `textual` is installed (importorskip), per
the plan's "test adapters/render data, not terminal pixels".
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from d2c.approvals import ApprovalCache
from d2c.tui import (
    ApprovalChoice,
    apply_choice,
    choice_from_key,
    completion_candidates,
    grouped_help_lines,
    is_textual_available,
    status_line,
    suggest_command,
    to_renderable,
    use_textual_ui,
)


def _req(command: str) -> SimpleNamespace:
    return SimpleNamespace(tool_name="Bash", tool_category="SHELL", tool_input={"command": command})


# ── approval choice mapping (Phase 52/64/65 semantics) ──────────────


def test_choice_from_key_is_case_sensitive_for_a():
    assert choice_from_key("y") is ApprovalChoice.ONCE
    assert choice_from_key("a") is ApprovalChoice.SESSION
    assert choice_from_key("A") is ApprovalChoice.ALWAYS  # uppercase = persistent
    assert choice_from_key("n") is ApprovalChoice.DENY
    assert choice_from_key("") is ApprovalChoice.DENY
    assert choice_from_key("garbage") is ApprovalChoice.DENY


def test_apply_choice_session_is_memory_only(tmp_dir):
    cache = ApprovalCache(path=tmp_dir / "approvals.json")
    req = _req("ls")
    assert apply_choice(ApprovalChoice.SESSION, cache, req) is True
    assert cache.session_count() == 1
    assert cache.persistent_count() == 0


def test_apply_choice_always_persists(tmp_dir):
    cache = ApprovalCache(path=tmp_dir / "approvals.json")
    req = _req("git status")
    assert apply_choice(ApprovalChoice.ALWAYS, cache, req) is True
    assert cache.persistent_count() == 1


def test_apply_choice_once_approves_without_caching(tmp_dir):
    cache = ApprovalCache(path=tmp_dir / "approvals.json")
    req = _req("pwd")
    assert apply_choice(ApprovalChoice.ONCE, cache, req) is True
    assert cache.session_count() == 0
    assert cache.persistent_count() == 0


def test_apply_choice_deny(tmp_dir):
    cache = ApprovalCache(path=tmp_dir / "approvals.json")
    assert apply_choice(ApprovalChoice.DENY, cache, _req("rm -rf /")) is False
    assert cache.runtime_count() == 0


# ── command registry reuse (Phase 72) ──────────────────────────────


def test_completion_candidates_include_top_level_and_subcommands():
    cands = completion_candidates()
    assert "/help" in cands
    assert "/approvals" in cands
    assert "/approvals clear-session" in cands
    assert "/profiles show" in cands


def test_grouped_help_lines_have_headings_and_commands():
    lines = grouped_help_lines()
    text = "\n".join(lines)
    for heading in ("Session", "State", "Safety", "Help"):
        assert heading in text
    assert "/approvals" in text
    assert "/profiles" in text


def test_suggest_command():
    assert suggest_command("/aprovals") == "/approvals"
    assert suggest_command("/zzzzzz") is None


# ── markdown + status line ──────────────────────────────────────────


def test_to_renderable_never_raises_and_returns_something():
    assert to_renderable("# Title\n\n- a\n- b") is not None
    assert to_renderable("plain text") is not None


def test_status_line_has_all_fields():
    line = status_line(
        model="deepseek-v4-pro",
        mode="default",
        trust=True,
        cwd="/repo",
        usage="10 in / 2 out",
        bg_tasks=1,
    )
    assert "model: deepseek-v4-pro" in line
    assert "mode: default" in line
    assert "trust: True" in line
    assert "cwd: /repo" in line
    assert "usage: 10 in / 2 out" in line
    assert "tasks: 1" in line


# ── gating + non-interactive boundary ───────────────────────────────


def test_use_textual_ui_reflects_env(monkeypatch):
    monkeypatch.delenv("D2C_TUI", raising=False)
    assert use_textual_ui() is False
    monkeypatch.setenv("D2C_TUI", "textual")
    assert use_textual_ui() is True
    monkeypatch.setenv("D2C_TUI", "prompt_toolkit")
    assert use_textual_ui() is False


def test_is_textual_available_returns_bool():
    assert isinstance(is_textual_available(), bool)


def test_importing_tui_does_not_import_textual_app():
    import d2c.tui  # noqa: F401

    # The Textual-dependent module is only loaded by run_textual_app(), so
    # merely importing the package (as helpers/tests do) must not pull it in.
    assert "d2c.tui.app" not in sys.modules


def test_importing_main_does_not_import_textual_app():
    import d2c.main  # noqa: F401

    assert "d2c.tui.app" not in sys.modules


# ── Textual app (only where textual is installed) ───────────────────


def test_textual_app_is_an_app_subclass():
    pytest.importorskip("textual")
    from textual.app import App

    from d2c.tui.app import D2CApp

    assert issubclass(D2CApp, App)


def test_textual_app_instantiates():
    pytest.importorskip("textual")
    from d2c.config import Config
    from d2c.main import ReplState
    from d2c.tui.app import D2CApp

    state = ReplState(config=Config(cwd="."), session_store=None, conversation=[])

    async def _noop_turn(_text):  # pragma: no cover - exercised only with textual
        if False:
            yield None

    app = D2CApp(state=state, run_turn=_noop_turn, active_bg_tasks=lambda: 0)
    assert app is not None


# ── Phase 75: approval modal view + tool timeline rows ──────────────


def _approval_req(*, tool_name, category, tool_input):
    return SimpleNamespace(
        tool_name=tool_name,
        tool_category=SimpleNamespace(value=category),
        tool_input=tool_input,
    )


def test_approval_view_bash_is_redacted_and_risk_labeled():
    from d2c.tui.approvals import approval_view

    req = _approval_req(tool_name="Bash", category="shell", tool_input={"command": "git status"})
    view = approval_view(req, SimpleNamespace(reason="needs approval"))
    assert view["tool"] == "Bash"
    assert view["category"] == "shell"
    assert "git status" in view["preview"]
    assert view["risk"] in ("allow", "ask", "deny")  # from the shell classifier


def test_approval_view_edit_shows_diff_summary_and_path():
    from d2c.tui.approvals import approval_view

    req = _approval_req(
        tool_name="Edit",
        category="write",
        tool_input={"file_path": "/proj/a.py", "old_string": "a\n", "new_string": "b\nc\n"},
    )
    view = approval_view(req, SimpleNamespace(reason="edit"))
    assert view["preview"] == "/proj/a.py"
    assert view["diff_summary"].startswith("+")  # e.g. "+2 / -1"
    assert view["diff_lines"]  # concrete diff lines present


def test_approval_view_generic_uses_input_preview():
    from d2c.tui.approvals import approval_view

    req = _approval_req(tool_name="Grep", category="read", tool_input={"pattern": "TODO"})
    view = approval_view(req, SimpleNamespace(reason="read"))
    assert "TODO" in view["preview"]


def test_tool_row_ok_error_denied():
    from d2c.tui.widgets import tool_row_from_event

    ok = SimpleNamespace(
        tool_use=SimpleNamespace(name="Read", input={"file_path": "src/x.py"}),
        result=SimpleNamespace(output="contents", error=False, metadata={}),
    )
    row = tool_row_from_event(ok)
    assert "Read" in row and "src/x.py" in row and "ok" in row

    denied = SimpleNamespace(
        tool_use=SimpleNamespace(name="Bash", input={"command": "rm -rf /"}),
        result=SimpleNamespace(output="Permission denied by rule", error=True, metadata={}),
    )
    assert "denied" in tool_row_from_event(denied)

    errored = SimpleNamespace(
        tool_use=SimpleNamespace(name="Bash", input={"command": "false"}),
        result=SimpleNamespace(output="boom: exit 1", error=True, metadata={}),
    )
    row = tool_row_from_event(errored)
    assert "error" in row and "boom" in row


def test_tool_row_shows_file_count_detail():
    from d2c.tui.widgets import tool_row_from_event

    ev = SimpleNamespace(
        tool_use=SimpleNamespace(name="ApplyPatch", input={}),
        result=SimpleNamespace(output="applied", error=False, metadata={"file_count": 3}),
    )
    assert "3 file(s)" in tool_row_from_event(ev)


def test_approval_modal_keys_map_to_scopes():
    pytest.importorskip("textual")
    from d2c.tui.app import ApprovalModal
    from d2c.tui.approvals import ApprovalChoice

    modal = ApprovalModal({"tool": "Bash", "category": "shell", "reason": "r"})
    captured: dict[str, object] = {}
    modal.dismiss = lambda value=None: captured.__setitem__("v", value)  # type: ignore[method-assign]

    def press(key, char):
        modal.on_key(SimpleNamespace(key=key, character=char))
        return captured["v"]

    assert press("escape", None) is ApprovalChoice.DENY  # default deny
    assert press("y", "y") is ApprovalChoice.ONCE
    assert press("a", "a") is ApprovalChoice.SESSION  # lowercase = session
    assert press("A", "A") is ApprovalChoice.ALWAYS  # uppercase = persistent
    assert press("n", "n") is ApprovalChoice.DENY
    assert press("enter", None) is ApprovalChoice.DENY  # unknown → deny
