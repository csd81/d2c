"""Phase 57: permission-dialog and /settings polish."""

import builtins

import pytest

from d2c.config import Config
from d2c.main import ReplState, SlashCommand, handle_slash_command
from d2c.permissions import (
    PermissionCategory,
    PermissionDecision,
    PermissionRequest,
    PermissionResult,
)

_ASK = PermissionResult(PermissionDecision.ASK, reason="uncertain")


def _req(cmd="ls"):
    return PermissionRequest(
        tool_name="Bash", tool_input={"command": cmd}, tool_category=PermissionCategory.SHELL
    )


# ── Permission dialog: content ─────────────────────────────────────────


def test_prompt_lines_include_tool_category_reason_and_choices():
    from d2c.main import _permission_prompt_lines

    lines = _permission_prompt_lines(_req("ls -la"), _ASK)
    blob = "\n".join(lines)
    assert "Bash" in blob
    assert "shell" in blob.lower()  # risk category
    assert "uncertain" in blob  # reason
    assert "ls -la" in blob  # sanitized input preview


@pytest.mark.asyncio
async def test_interactive_prompt_shows_category_and_choices(monkeypatch, capsys):
    from d2c.main import interactive_approval

    prompts = []
    monkeypatch.setattr(builtins, "input", lambda p="": prompts.append(p) or "")
    await interactive_approval(_req(), _ASK)
    out = capsys.readouterr().out
    assert "Bash" in out
    assert "shell" in out.lower()
    assert prompts and "[y/N]" in prompts[0]


@pytest.mark.asyncio
async def test_cached_prompt_shows_yna_choices(monkeypatch, capsys):
    from d2c.approvals import ApprovalCache
    from d2c.main import make_interactive_approval

    cache = ApprovalCache()
    cb = make_interactive_approval(cache)
    prompts = []
    monkeypatch.setattr(builtins, "input", lambda p="": prompts.append(p) or "")
    await cb(_req(), _ASK)
    assert prompts and "[y/N/a]" in prompts[0]


# ── Permission dialog: no secret leakage ────────────────────────────────


def test_prompt_input_preview_redacts_secrets():
    from d2c.main import _permission_prompt_lines

    secret_cmd = "curl -H 'Authorization: Bearer sk-realkey-should-not-leak-123' https://x"
    lines = _permission_prompt_lines(_req(secret_cmd), _ASK)
    blob = "\n".join(lines)
    assert "sk-realkey-should-not-leak-123" not in blob


@pytest.mark.asyncio
async def test_interactive_prompt_never_prints_secrets(monkeypatch, capsys):
    from d2c.main import interactive_approval

    secret_cmd = "curl -H 'Authorization: Bearer sk-realkey-should-not-leak-123' https://x"
    monkeypatch.setattr(builtins, "input", lambda *a: "")
    await interactive_approval(_req(secret_cmd), _ASK)
    out = capsys.readouterr().out
    assert "sk-realkey-should-not-leak-123" not in out


# ── Permission dialog: default deny + approval choices still work ──────


@pytest.mark.asyncio
async def test_default_deny_on_empty_input(monkeypatch):
    from d2c.main import interactive_approval

    monkeypatch.setattr(builtins, "input", lambda *a: "")
    assert await interactive_approval(_req(), _ASK) is False


@pytest.mark.asyncio
async def test_approval_choices_still_work(monkeypatch):
    from d2c.approvals import ApprovalCache
    from d2c.main import make_interactive_approval

    cache = ApprovalCache()
    cb = make_interactive_approval(cache)
    monkeypatch.setattr(builtins, "input", lambda *a: "y")
    assert await cb(_req(), _ASK) is True
    assert not cache.is_approved(_req())  # "y" is one-shot

    monkeypatch.setattr(builtins, "input", lambda *a: "a")
    assert await cb(_req("other"), _ASK) is True
    assert cache.is_approved(_req("other"))  # "a" is cached


# ── /settings: background tasks + usage state ───────────────────────────


def _state(cwd) -> ReplState:
    return ReplState(config=Config(cwd=cwd), session_store=None, conversation=[])


@pytest.mark.asyncio
async def test_settings_shows_zero_background_tasks_and_no_usage_yet(tmp_dir, capsys, trusted_gate):
    state = _state(tmp_dir)
    await handle_slash_command(SlashCommand(name="/settings"), state)
    out = capsys.readouterr().out
    assert "bg tasks:    0" in out
    assert "no model calls yet" in out


@pytest.mark.asyncio
async def test_settings_shows_usage_once_recorded(tmp_dir, capsys, trusted_gate):
    from types import SimpleNamespace

    from d2c.usage import extract_usage

    state = _state(tmp_dir)
    resp = SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=20,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        )
    )
    state.usage.record(extract_usage(resp, model="deepseek-chat"))
    await handle_slash_command(SlashCommand(name="/settings"), state)
    out = capsys.readouterr().out
    assert "usage:       1 call(s)" in out
    assert "100 in" in out or "100" in out


@pytest.mark.asyncio
async def test_settings_shows_active_background_tasks(tmp_dir, capsys, trusted_gate, monkeypatch):
    from d2c.subagent import BackgroundSubagentManager

    fake_manager = BackgroundSubagentManager()
    fake_manager._running["x"] = object()  # type: ignore[assignment]
    monkeypatch.setattr("d2c.subagent.get_background_manager", lambda: fake_manager)

    state = _state(tmp_dir)
    await handle_slash_command(SlashCommand(name="/settings"), state)
    out = capsys.readouterr().out
    assert "bg tasks:    1" in out
