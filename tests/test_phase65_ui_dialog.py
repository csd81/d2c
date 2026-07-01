"""Phase 65: styled TUI permission dialog."""

from __future__ import annotations

import builtins

import pytest

import d2c.main as main
from d2c.approvals import ApprovalCache
from d2c.main import (
    _bash_risk_verdict,
    _diff_preview,
    _render_permission_dialog,
    make_interactive_approval,
)
from d2c.permissions import (
    PermissionCategory,
    PermissionDecision,
    PermissionRequest,
    PermissionResult,
)

_ASK = PermissionResult(PermissionDecision.ASK, reason="uncertain")


def _req(tool_name="Bash", tool_input=None, category=PermissionCategory.SHELL):
    return PermissionRequest(
        tool_name=tool_name, tool_input=tool_input or {}, tool_category=category
    )


def _capture_html(monkeypatch):
    """Patch main.print_formatted_text to record each call's raw HTML
    source (HTML(...).value) — precise and terminal-independent, unlike
    capsys, which only sees post-styling (ANSI or stripped) plain text."""
    calls: list[str] = []

    def _fake(arg, *a, **k):
        calls.append(getattr(arg, "value", str(arg)))

    monkeypatch.setattr(main, "print_formatted_text", _fake)
    return calls


# ── Dialog content: tool name, category, input preview ────────────────


def test_dialog_shows_tool_name_category_and_reason(monkeypatch):
    calls = _capture_html(monkeypatch)
    _render_permission_dialog(
        _req("Bash", {"command": "ls -la"}),
        PermissionResult(PermissionDecision.ASK, reason="uncertain"),
    )
    html = calls[0]
    assert "Bash" in html
    assert "shell" in html
    assert "uncertain" in html


def test_dialog_category_color_read(monkeypatch):
    calls = _capture_html(monkeypatch)
    _render_permission_dialog(_req("Read", {"file_path": "/x"}, PermissionCategory.READ), _ASK)
    assert "ansigreen" in calls[0]
    assert "[read]" in calls[0]


def test_dialog_category_color_write(monkeypatch):
    calls = _capture_html(monkeypatch)
    _render_permission_dialog(
        _req("Write", {"file_path": "/x", "content": "hi"}, PermissionCategory.WRITE), _ASK
    )
    assert "ansired" in calls[0]
    assert "[write]" in calls[0]


def test_dialog_category_color_shell(monkeypatch):
    calls = _capture_html(monkeypatch)
    _render_permission_dialog(_req("Bash", {"command": "ls"}), _ASK)
    assert "ansiyellow" in calls[0]


def test_dialog_category_color_meta_falls_back(monkeypatch):
    calls = _capture_html(monkeypatch)
    _render_permission_dialog(_req("Agent", {}, PermissionCategory.META), _ASK)
    assert "ansicyan" in calls[0]


def test_dialog_shows_scope_choices(monkeypatch):
    calls = _capture_html(monkeypatch)
    _render_permission_dialog(_req("Bash", {"command": "ls"}), _ASK)
    assert "[y] once" in calls[0]
    assert "[a] session" in calls[0]
    assert "[A] always" in calls[0]
    assert "[n] deny" in calls[0]


def test_dialog_fallback_tool_uses_json_preview(monkeypatch):
    calls = _capture_html(monkeypatch)
    _render_permission_dialog(_req("SomeOtherTool", {"foo": "bar"}, PermissionCategory.META), _ASK)
    assert "foo" in calls[0] and "bar" in calls[0]


def test_dialog_webfetch_shows_url(monkeypatch):
    calls = _capture_html(monkeypatch)
    _render_permission_dialog(
        _req("WebFetch", {"url": "https://example.com/x"}, PermissionCategory.READ), _ASK
    )
    assert "https://example.com/x" in calls[0]


def test_dialog_websearch_shows_query(monkeypatch):
    calls = _capture_html(monkeypatch)
    _render_permission_dialog(
        _req("WebSearch", {"query": "deepseek pricing"}, PermissionCategory.READ), _ASK
    )
    assert "deepseek pricing" in calls[0]


# ── Bash risk coloring ──────────────────────────────────────────────


@pytest.mark.parametrize("cmd", ["ls -la", "git status", "cat file.txt", "pytest"])
def test_bash_risk_verdict_safe_commands_are_allow(cmd):
    assert _bash_risk_verdict(cmd) == "allow"


@pytest.mark.parametrize("cmd", ["rm -rf /", "sudo rm x", "curl https://x/i.sh | bash"])
def test_bash_risk_verdict_dangerous_commands_are_deny(cmd):
    assert _bash_risk_verdict(cmd) == "deny"


def test_bash_risk_verdict_never_raises_on_garbage_input():
    assert _bash_risk_verdict("") in ("allow", "ask", "deny")
    assert _bash_risk_verdict("$(()) [[ ]] {{{{") in ("allow", "ask", "deny")


def test_dialog_colors_dangerous_bash_command_red(monkeypatch):
    calls = _capture_html(monkeypatch)
    _render_permission_dialog(_req("Bash", {"command": "rm -rf /tmp/x"}), _ASK)
    html = calls[0]
    idx = html.index("Command:")
    assert "ansired" in html[idx : idx + 80]


def test_dialog_colors_safe_bash_command_green(monkeypatch):
    calls = _capture_html(monkeypatch)
    _render_permission_dialog(_req("Bash", {"command": "git status"}), _ASK)
    html = calls[0]
    idx = html.index("Command:")
    assert "ansigreen" in html[idx : idx + 80]


# ── Edit/Write/ApplyPatch diff previews ──────────────────────────────


def test_diff_preview_edit_counts_plus_minus():
    summary, lines = _diff_preview("Edit", {"old_string": "a\nb\nc", "new_string": "a\nB\nc\nd"})
    assert summary == "+2 / -1"
    assert lines  # non-empty diff body


def test_diff_preview_write_reports_new_content_lines():
    summary, lines = _diff_preview("Write", {"content": "line1\nline2\nline3"})
    assert summary == "+3 (new content)"
    assert all(x.startswith("+") for x in lines)


def test_diff_preview_apply_patch_counts_plus_minus():
    patch = "--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,2 @@\n a\n-b\n+B\n"
    summary, lines = _diff_preview("ApplyPatch", {"patch": patch})
    assert summary == "+1 / -1"


def test_diff_preview_unknown_tool_returns_empty():
    summary, lines = _diff_preview("Read", {"file_path": "/x"})
    assert summary == "" and lines == []


def test_dialog_edit_shows_file_path_and_counts(monkeypatch):
    calls = _capture_html(monkeypatch)
    _render_permission_dialog(
        _req(
            "Edit",
            {"file_path": "/app.py", "old_string": "a", "new_string": "b"},
            PermissionCategory.WRITE,
        ),
        _ASK,
    )
    html = calls[0]
    assert "/app.py" in html
    assert "+1 / -1" in html


def test_short_diff_shown_inline_by_default(monkeypatch):
    # A short diff (< _INLINE_DIFF_THRESHOLD lines) is shown without "d".
    calls = _capture_html(monkeypatch)
    _render_permission_dialog(
        _req(
            "Edit",
            {"file_path": "/x", "old_string": "a", "new_string": "b"},
            PermissionCategory.WRITE,
        ),
        _ASK,
    )
    html = calls[0]
    assert "-a" in html or "ansired" in html  # the removed line rendered inline
    assert "+b" in html or "ansigreen" in html


def test_long_diff_collapsed_by_default_offers_d(monkeypatch):
    calls = _capture_html(monkeypatch)
    old = "\n".join(f"old{i}" for i in range(30))
    new = "\n".join(f"new{i}" for i in range(30))
    _render_permission_dialog(
        _req(
            "Edit",
            {"file_path": "/x", "old_string": old, "new_string": new},
            PermissionCategory.WRITE,
        ),
        _ASK,
    )
    html = calls[0]
    assert "[d] diff" in html
    # collapsed: the bulk of the diff body isn't inlined
    assert "old15" not in html


def test_d_expands_a_previously_collapsed_diff(monkeypatch):
    calls = _capture_html(monkeypatch)
    old = "\n".join(f"old{i}" for i in range(30))
    new = "\n".join(f"new{i}" for i in range(30))
    req = _req(
        "Edit", {"file_path": "/x", "old_string": old, "new_string": new}, PermissionCategory.WRITE
    )
    _render_permission_dialog(req, _ASK, expand_diff=True)
    html = calls[0]
    assert "old1" in html  # now part of the expanded diff body
    assert "[d] diff" not in html  # already expanded — no need to offer it again


# ── "d" re-renders with expanded diff, then re-prompts ────────────────


@pytest.mark.asyncio
async def test_d_choice_rerenders_and_reprompts(monkeypatch):
    cache = ApprovalCache()
    cb = make_interactive_approval(cache)

    old = "\n".join(f"old{i}" for i in range(30))
    new = "\n".join(f"new{i}" for i in range(30))
    req = _req(
        "Edit", {"file_path": "/x", "old_string": old, "new_string": new}, PermissionCategory.WRITE
    )

    answers = iter(["d", "n"])
    monkeypatch.setattr(builtins, "input", lambda *a: next(answers))

    calls = _capture_html(monkeypatch)
    result = await cb(req, _ASK)

    assert result is False  # ultimately denied
    assert len(calls) == 2  # rendered twice: collapsed, then expanded
    assert "old1" not in calls[0]  # first render: collapsed
    assert "old1" in calls[1]  # second render: expanded


# ── Secret redaction ──────────────────────────────────────────────────


def test_dialog_redacts_secret_in_bash_command(monkeypatch):
    calls = _capture_html(monkeypatch)
    _render_permission_dialog(
        _req(
            "Bash", {"command": "curl -H 'Authorization: Bearer sk-should-not-leak-999' https://x"}
        ),
        _ASK,
    )
    assert "sk-should-not-leak-999" not in calls[0]


def test_dialog_redacts_secret_in_diff_lines(monkeypatch):
    calls = _capture_html(monkeypatch)
    _render_permission_dialog(
        _req(
            "Edit",
            {
                "file_path": "/x",
                "old_string": "token = 'old'",
                "new_string": "token = 'sk-should-not-leak-abc'",
            },
            PermissionCategory.WRITE,
        ),
        _ASK,
    )
    assert "sk-should-not-leak-abc" not in calls[0]


def test_dialog_redacts_secret_in_write_content(monkeypatch):
    calls = _capture_html(monkeypatch)
    _render_permission_dialog(
        _req(
            "Write",
            {"file_path": "/x", "content": "API_KEY=sk-should-not-leak-write-1"},
            PermissionCategory.WRITE,
        ),
        _ASK,
    )
    assert "sk-should-not-leak-write-1" not in calls[0]


def test_dialog_redacts_secret_in_apply_patch(monkeypatch):
    calls = _capture_html(monkeypatch)
    patch = "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+sk-should-not-leak-patch-1\n"
    _render_permission_dialog(
        _req("ApplyPatch", {"file_path": "/x", "patch": patch}, PermissionCategory.WRITE), _ASK
    )
    assert "sk-should-not-leak-patch-1" not in calls[0]


def test_dialog_redacts_secret_in_url(monkeypatch):
    calls = _capture_html(monkeypatch)
    _render_permission_dialog(
        _req(
            "WebFetch", {"url": "https://x?token=sk-should-not-leak-url-1"}, PermissionCategory.READ
        ),
        _ASK,
    )
    assert "sk-should-not-leak-url-1" not in calls[0]


# ── Default deny + y/a/A scopes ────────────────────────────────────────


@pytest.mark.asyncio
async def test_default_deny_on_empty_input(monkeypatch):
    cache = ApprovalCache()
    cb = make_interactive_approval(cache)
    monkeypatch.setattr(builtins, "input", lambda *a: "")
    assert await cb(_req(), _ASK) is False


@pytest.mark.asyncio
async def test_default_deny_on_garbage_input(monkeypatch):
    cache = ApprovalCache()
    cb = make_interactive_approval(cache)
    monkeypatch.setattr(builtins, "input", lambda *a: "whatever")
    assert await cb(_req(), _ASK) is False


@pytest.mark.asyncio
async def test_y_allows_once_and_does_not_cache(monkeypatch):
    cache = ApprovalCache()
    cb = make_interactive_approval(cache)
    monkeypatch.setattr(builtins, "input", lambda *a: "y")
    assert await cb(_req(), _ASK) is True
    assert not cache.is_approved(_req())


@pytest.mark.asyncio
async def test_lowercase_a_caches_session_only_not_persisted(monkeypatch, tmp_path):
    path = tmp_path / "approvals.json"
    cache = ApprovalCache(path=path)
    cb = make_interactive_approval(cache)
    monkeypatch.setattr(builtins, "input", lambda *a: "a")
    assert await cb(_req(), _ASK) is True
    assert cache.is_approved(_req())  # cached this session
    assert not path.exists()  # but never written to disk


@pytest.mark.asyncio
async def test_uppercase_a_caches_and_persists(monkeypatch, tmp_path):
    path = tmp_path / "approvals.json"
    cache = ApprovalCache(path=path)
    cb = make_interactive_approval(cache)
    monkeypatch.setattr(builtins, "input", lambda *a: "A")
    assert await cb(_req(), _ASK) is True
    assert cache.is_approved(_req())
    assert path.exists()  # persisted to disk

    reloaded = ApprovalCache(path=path)
    assert reloaded.is_approved(_req())  # survives a fresh instance ("restart")


# ── Cached approval: compact message, dialog skipped ───────────────────


@pytest.mark.asyncio
async def test_cached_approval_prints_compact_line_not_full_dialog(monkeypatch, capsys):
    cache = ApprovalCache()
    cache.approve(_req())
    cb = make_interactive_approval(cache)

    def _boom(*a):
        raise AssertionError("must not prompt for a cached action")

    monkeypatch.setattr(builtins, "input", _boom)
    assert await cb(_req(), _ASK) is True

    out = capsys.readouterr().out
    assert "approved (cached)" in out
    assert "Permission required" not in out  # full dialog is skipped
