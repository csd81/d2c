"""Phase 40: remaining hook events — inventory, timing, payloads, isolation."""

from functools import partial

import pytest

import d2c.main as main
from d2c.config import Config
from d2c.hooks import HookDefinition, HookEvent, HookRegistry, HookResult, HookType
from d2c.main import ReplState, SlashCommand, handle_slash_command
from d2c.persistence import SessionManager
from d2c.tools import set_active_hooks, set_file_history_tracker

# Categorization of every HookEvent. Update this when wiring/retiring events.
FIRED = {
    HookEvent.SESSION_START,
    HookEvent.SESSION_END,
    HookEvent.SETUP,
    HookEvent.STOP,
    HookEvent.USER_PROMPT_SUBMIT,
    HookEvent.PRE_TOOL_USE,
    HookEvent.POST_TOOL_USE,
    HookEvent.POST_TOOL_USE_FAILURE,
    HookEvent.PERMISSION_DENIED,
    HookEvent.PRE_COMPACT,
    HookEvent.POST_COMPACT,
    HookEvent.SUBAGENT_START,
    HookEvent.SUBAGENT_STOP,
    HookEvent.WORKTREE_CREATE,
    HookEvent.WORKTREE_REMOVE,
    HookEvent.TASK_CREATED,
    HookEvent.TASK_COMPLETED,
    HookEvent.FILE_CHANGED,
    HookEvent.INSTRUCTIONS_LOADED,
}
# No current runtime source — intentionally inactive (documented, not broken).
INTENTIONALLY_INACTIVE = {
    HookEvent.CONFIG_CHANGE,  # config is immutable after load
    HookEvent.CWD_CHANGED,  # cwd is immutable after load
    HookEvent.ELICITATION,
    HookEvent.ELICITATION_RESULT,  # no elicitation flow
    HookEvent.NOTIFICATION,  # no notification surface
    HookEvent.PERMISSION_REQUEST,  # executors don't interactively prompt
    HookEvent.STOP_FAILURE,  # no stop-failure path
    HookEvent.TEAMMATE_IDLE,  # no multi-agent teams
}


@pytest.fixture(autouse=True)
def _reset_runtime():
    yield
    set_active_hooks(None)
    set_file_history_tracker(None)


def _recording_registry(*events):
    """A HookRegistry with a recording callback on each given event.

    Returns (registry, captured) where captured is a list of (event, payload).
    """
    captured: list[tuple] = []
    reg = HookRegistry()
    for ev in events:

        async def cb(ctx, _ev=ev):
            captured.append((_ev, ctx))
            return HookResult()

        reg.register(HookDefinition(event=ev, hook_type=HookType.CALLBACK, callback=cb))
    return reg, captured


# ── 1. Inventory ──────────────────────────────────────────────────────


def test_every_hook_event_is_categorized():
    all_events = set(HookEvent)
    assert FIRED.isdisjoint(INTENTIONALLY_INACTIVE)
    assert FIRED | INTENTIONALLY_INACTIVE == all_events, all_events - (
        FIRED | INTENTIONALLY_INACTIVE
    )


# ── 4. FILE_CHANGED timing + payload ──────────────────────────────────


@pytest.mark.asyncio
async def test_file_changed_fires_after_write(tmp_dir, trusted_gate):
    from d2c.tools.write_tool import FileWriteTool

    reg, captured = _recording_registry(HookEvent.FILE_CHANGED)
    set_active_hooks(reg)

    f = tmp_dir / "new.txt"
    res = await FileWriteTool().execute(file_path=str(f), content="hello")
    assert not res.error

    assert len(captured) == 1
    ev, payload = captured[0]
    assert ev == HookEvent.FILE_CHANGED
    assert payload["path"] == str(f)
    assert payload["tool"] == "Write"
    assert payload["operation"] == "write"
    # payload must not carry file contents / secrets
    assert "hello" not in str(payload)


@pytest.mark.asyncio
async def test_file_changed_fires_after_edit(tmp_dir, trusted_gate):
    from d2c.tools.edit_tool import FileEditTool
    from d2c.tools.read_tool import FileReadTool

    reg, captured = _recording_registry(HookEvent.FILE_CHANGED)
    set_active_hooks(reg)

    f = tmp_dir / "code.py"
    f.write_text("x = 1\n")
    await FileReadTool().execute(file_path=str(f))
    res = await FileEditTool().execute(file_path=str(f), old_string="x = 1", new_string="x = 2")
    assert not res.error
    assert [c[1]["operation"] for c in captured] == ["edit"]


@pytest.mark.asyncio
async def test_file_changed_not_fired_on_failed_write(tmp_dir, trusted_gate):
    from d2c.tools.write_tool import FileWriteTool

    reg, captured = _recording_registry(HookEvent.FILE_CHANGED)
    set_active_hooks(reg)

    f = tmp_dir / "existing.txt"
    f.write_text("orig")
    # Overwriting an existing file without reading first fails → no FILE_CHANGED.
    res = await FileWriteTool().execute(file_path=str(f), content="new")
    assert res.error
    assert captured == []


# ── 3. Session lifecycle on /clear ────────────────────────────────────


@pytest.mark.asyncio
async def test_clear_fires_session_lifecycle(tmp_dir, monkeypatch):
    monkeypatch.setattr(main, "SessionManager", partial(SessionManager, base_dir=tmp_dir))
    old = SessionManager(base_dir=tmp_dir).create_session(tmp_dir)

    reg, captured = _recording_registry(HookEvent.SESSION_END, HookEvent.SESSION_START)
    set_active_hooks(reg)

    state = ReplState(config=Config(cwd=tmp_dir), session_store=old, conversation=[])
    await handle_slash_command(SlashCommand(name="/clear"), state)

    events = [e for e, _ in captured]
    assert HookEvent.SESSION_END in events
    assert HookEvent.SESSION_START in events
    # SESSION_END references the old session, SESSION_START the new one.
    end_payload = next(p for e, p in captured if e == HookEvent.SESSION_END)
    start_payload = next(p for e, p in captured if e == HookEvent.SESSION_START)
    assert end_payload["session_id"] == old.session_id
    assert start_payload["session_id"] == state.session_store.session_id
    assert start_payload["session_id"] != old.session_id


# ── 7. Observability hook failure does not crash the tool ─────────────


@pytest.mark.asyncio
async def test_observability_hook_failure_is_isolated(tmp_dir, trusted_gate):
    from d2c.tools.write_tool import FileWriteTool

    reg = HookRegistry()

    async def boom(ctx):
        raise RuntimeError("hook blew up")

    reg.register(
        HookDefinition(event=HookEvent.FILE_CHANGED, hook_type=HookType.CALLBACK, callback=boom)
    )
    set_active_hooks(reg)

    f = tmp_dir / "ok.txt"
    res = await FileWriteTool().execute(file_path=str(f), content="data")
    # The write still succeeds despite the failing hook.
    assert not res.error
    assert f.read_text() == "data"
