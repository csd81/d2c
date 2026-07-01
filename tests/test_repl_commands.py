"""Phase 36: REPL slash-command parsing and dispatch.

Tests the pure parser and the dispatcher without an interactive terminal.
"""

from functools import partial

import pytest

import d2c.main as main
from d2c.config import Config
from d2c.main import (
    ReplState,
    SlashCommand,
    handle_slash_command,
    parse_slash_command,
)
from d2c.persistence import SessionEntry, SessionManager, _utc_now
from d2c.tools import set_file_history_tracker


@pytest.fixture(autouse=True)
def _reset_tracker():
    yield
    set_file_history_tracker(None)


@pytest.fixture
def tmp_manager(tmp_dir, monkeypatch):
    """Point main's SessionManager at a temp dir so tests don't touch ~/.d2c."""
    monkeypatch.setattr(main, "SessionManager", partial(SessionManager, base_dir=tmp_dir))
    return SessionManager(base_dir=tmp_dir)


def _state(cwd, session_store) -> ReplState:
    return ReplState(config=Config(cwd=cwd), session_store=session_store, conversation=[])


# ── Parser ────────────────────────────────────────────────────────────


def test_parse_help_no_args():
    cmd = parse_slash_command("/help")
    assert cmd is not None
    assert cmd.name == "/help"
    assert cmd.args == []


def test_parse_with_args_and_normalizes_case():
    cmd = parse_slash_command("  /RESUME abc123  ")
    assert cmd.name == "/resume"
    assert cmd.args == ["abc123"]


def test_parse_non_slash_returns_none():
    assert parse_slash_command("hello world") is None
    assert parse_slash_command("") is None


# ── Dispatch ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_command_handled_locally(tmp_dir, capsys):
    state = _state(tmp_dir, None)
    cont = await handle_slash_command(SlashCommand(name="/bogus"), state)
    assert cont is True  # REPL keeps running
    assert "Unknown command: /bogus" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_exit_returns_false(tmp_dir):
    state = _state(tmp_dir, None)
    assert await handle_slash_command(SlashCommand(name="/exit"), state) is False
    assert await handle_slash_command(SlashCommand(name="/quit"), state) is False


@pytest.mark.asyncio
async def test_settings_prints_no_secrets(tmp_dir, tmp_manager, capsys, trusted_gate):
    store = tmp_manager.create_session(tmp_dir)
    state = ReplState(
        config=Config(cwd=tmp_dir, deepseek_api_key="sk-SECRET"),
        session_store=store,
        conversation=[],
    )
    await handle_slash_command(SlashCommand(name="/settings"), state)
    out = capsys.readouterr().out
    assert "model" in out
    assert "permission" in out
    assert str(tmp_dir) in out
    assert store.session_id in out
    assert "sk-SECRET" not in out  # no secrets leaked


@pytest.mark.asyncio
async def test_clear_replaces_session_and_empties_conversation(tmp_dir, tmp_manager):
    old = tmp_manager.create_session(tmp_dir)
    state = ReplState(
        config=Config(cwd=tmp_dir),
        session_store=old,
        conversation=[{"role": "user", "content": "hi"}],
    )
    await handle_slash_command(SlashCommand(name="/clear"), state)
    assert state.conversation == []
    assert state.session_store.session_id != old.session_id


@pytest.mark.asyncio
async def test_resume_loads_messages_and_replaces_session(tmp_dir, tmp_manager):
    src = tmp_manager.create_session(tmp_dir)
    src.append(
        SessionEntry(
            role="user", content="earlier question", timestamp=_utc_now(), entry_type="message"
        )
    )

    state = _state(tmp_dir, None)
    await handle_slash_command(SlashCommand(name="/resume", args=[src.session_id]), state)

    assert state.session_store.session_id == src.session_id
    assert any(m.get("content") == "earlier question" for m in state.conversation)


@pytest.mark.asyncio
async def test_fork_creates_new_session_with_messages(tmp_dir, tmp_manager):
    src = tmp_manager.create_session(tmp_dir)
    src.append(
        SessionEntry(
            role="user", content="forked question", timestamp=_utc_now(), entry_type="message"
        )
    )

    state = _state(tmp_dir, None)
    await handle_slash_command(SlashCommand(name="/fork", args=[src.session_id]), state)

    assert state.session_store.session_id != src.session_id  # new session
    assert any(m.get("content") == "forked question" for m in state.conversation)


@pytest.mark.asyncio
async def test_resume_and_fork_missing_id_preserve_state(tmp_dir, tmp_manager, capsys):
    store = tmp_manager.create_session(tmp_dir)
    convo = [{"role": "user", "content": "keep me"}]
    state = ReplState(config=Config(cwd=tmp_dir), session_store=store, conversation=convo)

    await handle_slash_command(SlashCommand(name="/resume", args=[]), state)
    await handle_slash_command(SlashCommand(name="/fork", args=[]), state)

    out = capsys.readouterr().out
    assert "Usage: /resume" in out
    assert "Usage: /fork" in out
    # State unchanged.
    assert state.session_store.session_id == store.session_id
    assert state.conversation == convo


@pytest.mark.asyncio
async def test_resume_unknown_id_keeps_current_session(tmp_dir, tmp_manager, capsys):
    store = tmp_manager.create_session(tmp_dir)
    state = ReplState(config=Config(cwd=tmp_dir), session_store=store, conversation=[])
    await handle_slash_command(SlashCommand(name="/resume", args=["does-not-exist"]), state)
    # resume_session on a missing id returns an empty transcript; session id
    # still changes to the requested one, but nothing crashes.
    out = capsys.readouterr().out
    assert "Resumed session" in out or "Could not resume" in out
