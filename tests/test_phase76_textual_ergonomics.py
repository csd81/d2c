"""Phase 76: Textual session ergonomics.

Pure helpers (input history, tool status) are tested directly; scrollback,
keybindings, history navigation, and the approval-modal Escape path are driven
with Textual's pilot where `textual` is installed.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest

from d2c.tui.widgets import InputHistory, tool_row_status

# Deterministic pilot tests: instant (non-animated) scrolling so scroll_offset
# is settled when we assert on it. Set before Textual is first imported (its
# import happens lazily inside the pilot tests below); d2c.tui.widgets above
# does not import Textual.
os.environ.setdefault("TEXTUAL_ANIMATIONS", "none")


# ── pure: InputHistory ──────────────────────────────────────────────


def test_input_history_prev_next_walk():
    h = InputHistory()
    h.add("one")
    h.add("two")
    assert h.navigating is False
    assert h.prev() == "two"  # newest first
    assert h.navigating is True
    assert h.prev() == "one"
    assert h.prev() == "one"  # clamps at oldest
    assert h.next() == "two"
    assert h.next() == ""  # past newest → empty
    assert h.navigating is False


def test_input_history_dedupes_consecutive_and_ignores_blank():
    h = InputHistory()
    h.add("same")
    h.add("same")
    h.add("  ")  # blank ignored
    assert h.prev() == "same"
    assert h.prev() == "same"  # only one entry


def test_input_history_next_without_navigating_is_none():
    h = InputHistory()
    h.add("x")
    assert h.next() is None  # not navigating yet


def test_input_history_empty():
    h = InputHistory()
    assert h.prev() is None
    assert h.next() is None


# ── pure: tool status distinctions ──────────────────────────────────


def _ev(output: str, error: bool):
    return SimpleNamespace(
        tool_use=SimpleNamespace(name="Bash", input={"command": "x"}),
        result=SimpleNamespace(output=output, error=error, metadata={}),
    )


def test_tool_row_status_distinct():
    assert tool_row_status(_ev("ok", False)) == "ok"
    assert tool_row_status(_ev("boom", True)) == "error"
    assert tool_row_status(_ev("Permission denied by rule", True)) == "denied"


# ── Textual pilot: scrollback, history, keys, modal ─────────────────


def _app(**overrides):
    from d2c.approvals import ApprovalCache
    from d2c.config import Config
    from d2c.main import ReplState
    from d2c.tui.app import D2CApp

    async def _noop_turn(_text):  # pragma: no cover - depends on textual
        if False:
            yield None

    cache = overrides.get("cache") or ApprovalCache()
    state = ReplState(config=Config(cwd="."), session_store=None, conversation=[], approvals=cache)
    return D2CApp(
        state=state,
        run_turn=overrides.get("run_turn", _noop_turn),
        active_bg_tasks=lambda: 0,
        approval_holder=overrides.get("approval_holder"),
    ), state


def test_scrollback_follows_at_bottom_but_not_when_scrolled_up():
    pytest.importorskip("textual")
    app, _ = _app()

    async def run():
        async with app.run_test() as pilot:
            await pilot.pause()
            for i in range(80):
                app._write_system(f"line {i}")
            await pilot.pause()
            log = app._transcript()
            assert app._at_bottom(log)  # auto-followed to the newest output

            log.scroll_home(animate=False)
            await pilot.pause()
            y = log.scroll_offset.y
            assert not app._at_bottom(log)  # we are scrolled up
            app._write_system("arrived while scrolled up")
            await pilot.pause()
            assert log.scroll_offset.y == y  # viewport not yanked down

            app.action_scroll_end()
            await pilot.pause()
            await pilot.pause()
            assert app._at_bottom(log)  # End returns to latest

    asyncio.run(run())


def test_up_down_navigate_input_history():
    pytest.importorskip("textual")
    from textual.widgets import Input

    app, _ = _app()

    async def run():
        async with app.run_test() as pilot:
            inp = app.query_one("#prompt", Input)
            inp.focus()
            await pilot.pause()
            for prompt in ("one", "two"):
                inp.value = prompt
                await pilot.press("enter")
                await pilot.pause()
            assert inp.value == ""
            await pilot.press("up")
            await pilot.pause()
            assert inp.value == "two"
            await pilot.press("up")
            await pilot.pause()
            assert inp.value == "one"
            await pilot.press("down")
            await pilot.pause()
            assert inp.value == "two"
            await pilot.press("down")
            await pilot.pause()
            assert inp.value == ""

    asyncio.run(run())


def test_ctrl_l_clears_transcript_only():
    pytest.importorskip("textual")
    app, state = _app()

    async def run():
        async with app.run_test() as pilot:
            await pilot.pause()
            app._write_system("keep me?")
            await pilot.pause()
            assert len(app._transcript().lines) > 0
            state.conversation.append({"role": "user", "content": "x"})

            app.action_clear_transcript()
            await pilot.pause()
            assert len(app._transcript().lines) == 0  # view cleared
            assert state.conversation == [{"role": "user", "content": "x"}]  # session intact

    asyncio.run(run())


def test_escape_closes_approval_modal_as_deny():
    pytest.importorskip("textual")
    app, _ = _app()
    req = SimpleNamespace(
        tool_name="Bash",
        tool_category=SimpleNamespace(value="shell"),
        tool_input={"command": "ls"},
    )
    res = SimpleNamespace(reason="needs approval")
    out: dict[str, object] = {}

    async def run():
        async with app.run_test() as pilot:

            async def _ask():
                out["approved"] = await app.request_approval(req, res)

            app.run_worker(_ask(), name="ask")
            await pilot.pause()
            await asyncio.sleep(0.2)
            await pilot.pause()
            await pilot.press("escape")
            await asyncio.sleep(0.2)
            await pilot.pause()

    asyncio.run(run())
    assert out["approved"] is False


def test_small_terminal_layout_does_not_crash():
    pytest.importorskip("textual")
    app, _ = _app()

    async def run():
        async with app.run_test(size=(20, 5)) as pilot:
            await pilot.pause()
            assert app.query_one("#transcript") is not None
            assert app.query_one("#status") is not None
            assert app.query_one("#prompt") is not None

    asyncio.run(run())
