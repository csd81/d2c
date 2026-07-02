"""Phase 78: Textual clickable approvals + selection mode.

Pure button→choice mapping is tested directly; clickable approvals and the
selection-mode toggle are driven with Textual's pilot where `textual` is
installed. Keyboard approval behavior is asserted unchanged.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest

from d2c.tui.approvals import ApprovalChoice, choice_from_button

os.environ.setdefault("TEXTUAL_ANIMATIONS", "none")


# ── pure: button → choice ───────────────────────────────────────────


def test_choice_from_button_maps_all_and_denies_unknown():
    assert choice_from_button("deny") is ApprovalChoice.DENY
    assert choice_from_button("once") is ApprovalChoice.ONCE
    assert choice_from_button("session") is ApprovalChoice.SESSION
    assert choice_from_button("always") is ApprovalChoice.ALWAYS
    assert choice_from_button("bogus") is ApprovalChoice.DENY
    assert choice_from_button(None) is ApprovalChoice.DENY


# ── helpers ─────────────────────────────────────────────────────────


def _app(cache=None):
    from d2c.approvals import ApprovalCache
    from d2c.config import Config
    from d2c.main import ReplState
    from d2c.tui.app import D2CApp

    async def _noop_turn(_text):  # pragma: no cover - depends on textual
        if False:
            yield None

    # NB: an empty ApprovalCache is falsy (__len__ == 0), so use an explicit
    # None check rather than `cache or ApprovalCache()`.
    cache = cache if cache is not None else ApprovalCache()
    state = ReplState(config=Config(cwd="."), session_store=None, conversation=[], approvals=cache)
    return D2CApp(state=state, run_turn=_noop_turn, active_bg_tasks=lambda: 0), state


def _req():
    return SimpleNamespace(
        tool_name="Bash",
        tool_category=SimpleNamespace(value="shell"),
        tool_input={"command": "ls"},
    )


async def _settle(pilot):
    """Wait for the modal + its buttons to be mounted before interacting."""
    await pilot.pause()
    await asyncio.sleep(0.2)
    await pilot.pause()


def _click_choice(button_id, tmp_dir):
    """Open the approval modal and activate one button; return (approved, cache).

    Uses Button.press() (posts the same Pressed message a mouse click does),
    which is deterministic — unlike coordinate clicks, which race modal layout
    for the rightmost buttons under the test harness."""
    from textual.widgets import Button

    from d2c.approvals import ApprovalCache
    from d2c.config import Config
    from d2c.main import ReplState
    from d2c.tui.app import D2CApp

    cache = ApprovalCache(path=tmp_dir / "approvals.json")

    async def _noop_turn(_text):
        if False:
            yield None

    state = ReplState(config=Config(cwd="."), session_store=None, conversation=[], approvals=cache)
    app = D2CApp(state=state, run_turn=_noop_turn, active_bg_tasks=lambda: 0)
    out: dict[str, object] = {}

    async def run():
        async with app.run_test() as pilot:

            async def _ask():
                out["approved"] = await app.request_approval(_req(), SimpleNamespace(reason="x"))

            app.run_worker(_ask(), name="ask")
            await _settle(pilot)
            app.screen.query_one(f"#{button_id}", Button).press()
            await asyncio.sleep(0.2)
            await pilot.pause()

    asyncio.run(run())
    return out.get("approved"), cache


# ── Textual pilot: clickable approvals ──────────────────────────────


def test_modal_has_four_clickable_choices():
    pytest.importorskip("textual")
    from textual.widgets import Button

    app, _ = _app()
    ids: set[str] = set()

    async def run():
        async with app.run_test() as pilot:

            async def _ask():
                await app.request_approval(_req(), SimpleNamespace(reason="x"))

            app.run_worker(_ask(), name="ask")
            await _settle(pilot)
            for b in app.screen.query(Button):
                ids.add(b.id)
            await pilot.press("escape")
            await asyncio.sleep(0.1)
            await pilot.pause()

    asyncio.run(run())
    assert ids == {"deny", "once", "session", "always"}


def test_click_deny(tmp_dir):
    pytest.importorskip("textual")
    approved, cache = _click_choice("deny", tmp_dir)
    assert approved is False
    assert cache.runtime_count() == 0


def test_click_once(tmp_dir):
    pytest.importorskip("textual")
    approved, cache = _click_choice("once", tmp_dir)
    assert approved is True
    assert cache.session_count() == 0
    assert cache.persistent_count() == 0


def test_click_session(tmp_dir):
    pytest.importorskip("textual")
    approved, cache = _click_choice("session", tmp_dir)
    assert approved is True
    assert cache.session_count() == 1
    assert cache.persistent_count() == 0


def test_click_always(tmp_dir):
    pytest.importorskip("textual")
    approved, cache = _click_choice("always", tmp_dir)
    assert approved is True
    assert cache.persistent_count() == 1


# ── keyboard approval unchanged ─────────────────────────────────────


def test_keyboard_shortcuts_still_map():
    pytest.importorskip("textual")
    from d2c.tui.app import ApprovalModal

    modal = ApprovalModal({"tool": "Bash", "category": "shell", "reason": "r"})
    captured: dict[str, object] = {}
    modal.dismiss = lambda value=None: captured.__setitem__("v", value)  # type: ignore[method-assign]

    def press(key, char):
        modal.on_key(SimpleNamespace(key=key, character=char))
        return captured["v"]

    assert press("escape", None) is ApprovalChoice.DENY
    assert press("enter", None) is ApprovalChoice.DENY
    assert press("y", "y") is ApprovalChoice.ONCE
    assert press("a", "a") is ApprovalChoice.SESSION
    assert press("A", "A") is ApprovalChoice.ALWAYS
    assert press("n", "n") is ApprovalChoice.DENY


def test_unknown_key_is_ignored_not_dismissed():
    pytest.importorskip("textual")
    from d2c.tui.app import ApprovalModal

    modal = ApprovalModal({"tool": "Bash", "category": "shell", "reason": "r"})
    calls: list[object] = []
    modal.dismiss = lambda value=None: calls.append(value)  # type: ignore[method-assign]
    modal.on_key(SimpleNamespace(key="tab", character=None))  # navigation key
    assert calls == []  # nothing dismissed → safe no-op


# ── selection mode ──────────────────────────────────────────────────


def test_selection_mode_toggles_state_and_status():
    pytest.importorskip("textual")
    app, _ = _app()
    assert app._selection_mode is False
    assert "SELECT" not in app._status_text()

    app.action_toggle_selection()
    assert app._selection_mode is True
    assert "SELECT" in app._status_text()

    app.action_toggle_selection()
    assert app._selection_mode is False
    assert "SELECT" not in app._status_text()
