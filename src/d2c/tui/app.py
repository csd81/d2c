"""Phase 74: the Textual application shell (EXPERIMENTAL, opt-in).

Imports ``textual`` at module load, so this module must only be imported when
Textual is installed (via :func:`d2c.tui.run_textual_app`). The heavy logic
lives in the Textual-free helpers (``commands``/``approvals``/``markdown``/
``widgets``) and in the ``run_turn`` closure passed in from ``main`` — this
class is thin glue: a transcript log, a status footer, and an input box.
"""

from __future__ import annotations

import contextlib
import io
from typing import Any, cast

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, RichLog, Static

from d2c.loop import StopEvent, TextDelta, ToolExecutionEvent
from d2c.loop import TextResponse as LoopTextResponse
from d2c.tui.approvals import (
    APPROVAL_BUTTONS,
    ApprovalChoice,
    apply_choice,
    approval_view,
    choice_from_button,
    choice_from_key,
)
from d2c.tui.markdown import to_renderable
from d2c.tui.widgets import InputHistory, status_line, tool_row_from_event, tool_row_status

_APPROVAL_KEYS = ("y", "a", "A", "n")  # keyboard shortcuts recognized by the modal

_MODAL_DIFF_MAX_LINES = 8  # short inline diff preview in the approval modal

# Phase 76: per-role transcript styling (restrained, terminal-friendly).
_USER_STYLE = "bold cyan"
_SYSTEM_STYLE = "dim"
_ERROR_STYLE = "bold red"
_TOOL_STATUS_STYLE = {"ok": "green", "error": "bold red", "denied": "bold yellow"}


class ApprovalModal(ModalScreen):
    """Phase 75: permission approval modal. Collects a choice only; the caller
    (D2CApp.request_approval) applies it via the existing approval cache, so
    approval semantics live in one place. Deny is the default (Escape/Enter/
    unknown key)."""

    CSS = """
    ApprovalModal { align: center middle; }
    #approval-box { width: 80%; max-width: 100; padding: 1 2; border: thick $warning; background: $surface; }
    #approval-buttons { height: auto; align: center middle; }
    #approval-buttons Button { margin: 1 1 0 1; }
    """

    def __init__(self, view: dict[str, Any]) -> None:
        super().__init__()
        self._view = view

    def compose(self) -> ComposeResult:
        v = self._view
        lines = [
            f"Permission required: {v['tool']} [{v['category']}]",
            f"Reason: {v['reason']}",
        ]
        if v.get("preview"):
            lines.append(f"Input:  {v['preview']}")
        if v.get("risk"):
            lines.append(f"Risk:   {v['risk']}")
        if v.get("diff_summary"):
            lines.append(f"Diff:   {v['diff_summary']}")
            for dl in list(v.get("diff_lines") or [])[:_MODAL_DIFF_MAX_LINES]:
                lines.append(f"  {dl}")
        lines.append("")
        lines.append("Click a choice, or press [y] once  [a] session  [A] always  [n]/Esc deny")
        # Phase 78: clickable choices. Buttons are not keyboard-focusable so
        # Enter/Space never activate one — the keyboard path (on_key) stays the
        # single authority for key input (Enter = deny), and mouse clicks route
        # through on_button_pressed. Both funnel into the same dismiss(choice).
        buttons = []
        for label, button_id, variant in APPROVAL_BUTTONS:
            btn = Button(label, id=button_id, variant=cast(Any, variant))
            btn.can_focus = False
            buttons.append(btn)
        yield Vertical(
            Static("\n".join(lines)),
            Horizontal(*buttons, id="approval-buttons"),
            id="approval-box",
        )

    def on_button_pressed(self, event: Any) -> None:
        self.dismiss(choice_from_button(event.button.id))

    def on_key(self, event: Any) -> None:
        # Keyboard behavior unchanged from Phase 75: Esc/Enter deny; y/a/A/n map
        # to their scopes; any other key is ignored (safe no-op).
        if event.key in ("escape", "enter"):
            self.dismiss(ApprovalChoice.DENY)
            return
        candidate = event.character or event.key
        if candidate in _APPROVAL_KEYS:
            self.dismiss(choice_from_key(candidate))


class D2CApp(App):
    """A minimal transcript + input + status Textual app driving the agent loop."""

    CSS = """
    #transcript { height: 1fr; border: round $panel; }
    #status { height: 1; color: $text-muted; overflow-x: hidden; }
    #prompt { dock: bottom; }
    """

    # Phase 76: keyboard ergonomics. Home/End collide with the Input's cursor
    # keys, but the focused Input consumes them first, so these only fire when
    # the transcript is focused — no conflict while typing.
    BINDINGS = [
        Binding("ctrl+c", "cancel", "Cancel/Quit", show=False, priority=True),
        Binding("ctrl+l", "clear_transcript", "Clear view", show=False),
        Binding("ctrl+s", "toggle_selection", "Select mode", show=False),
        Binding("pageup", "scroll_up", "Scroll up", show=False),
        Binding("pagedown", "scroll_down", "Scroll down", show=False),
        Binding("end", "scroll_end", "To latest", show=False),
        Binding("home", "scroll_home", "To top", show=False),
    ]

    def __init__(
        self,
        *,
        state: Any,
        run_turn: Any,
        active_bg_tasks: Any,
        approval_holder: Any = None,
    ) -> None:
        super().__init__()
        self._state = state
        self._run_turn = run_turn
        self._active_bg_tasks = active_bg_tasks
        self._approval_holder = approval_holder
        self._history = InputHistory()
        self._selection_mode = False

    def on_mount(self) -> None:
        # Phase 75: route the agent loop's approval requests to the Textual
        # modal (instead of the prompt_toolkit/stdin fallback).
        if self._approval_holder is not None:
            self._approval_holder.approval_cb = self.request_approval
        with contextlib.suppress(Exception):
            self.query_one("#prompt", Input).focus()

    # ── scrollback + role-styled writes (Phase 76) ──────────────────
    def _transcript(self) -> RichLog:
        return self.query_one("#transcript", RichLog)

    @staticmethod
    def _at_bottom(log: RichLog) -> bool:
        """Whether the transcript is scrolled to the latest output."""
        try:
            return bool(log.is_vertical_scroll_end)
        except Exception:
            try:
                return log.scroll_offset.y >= log.max_scroll_y
            except Exception:
                return True

    def _write(self, renderable: Any) -> None:
        """Write to the transcript, following the tail only if the user was
        already at the bottom (don't yank the viewport if they scrolled up)."""
        log = self._transcript()
        log.write(renderable, scroll_end=self._at_bottom(log))

    def _write_user(self, text: str) -> None:
        self._write(Text(f"› {text}", style=_USER_STYLE))

    def _write_system(self, text: str, *, style: str = _SYSTEM_STYLE) -> None:
        self._write(Text(text, style=style))

    def _write_tool(self, event: Any) -> None:
        style = _TOOL_STATUS_STYLE.get(tool_row_status(event), "")
        self._write(Text(tool_row_from_event(event), style=style))

    # ── keyboard actions (Phase 76) ─────────────────────────────────
    def action_scroll_up(self) -> None:
        self._transcript().scroll_page_up()

    def action_scroll_down(self) -> None:
        self._transcript().scroll_page_down()

    def action_scroll_home(self) -> None:
        self._transcript().scroll_home()

    def action_scroll_end(self) -> None:
        self._transcript().scroll_end()

    def action_clear_transcript(self) -> None:
        # Visual only — never touches session/conversation/history state.
        self._transcript().clear()

    def _set_mouse_tracking(self, enabled: bool) -> None:
        """Best-effort pause/resume of Textual's terminal mouse reporting so the
        terminal's native selection works. Not all drivers expose this; if not,
        selection mode still guides the user to Shift+drag (see status bar)."""
        driver = getattr(self, "_driver", None)
        if driver is None:
            return
        name = "_enable_mouse_support" if enabled else "_disable_mouse_support"
        fn = getattr(driver, name, None)
        if callable(fn):
            with contextlib.suppress(Exception):
                fn()

    def action_toggle_selection(self) -> None:
        # Phase 78: toggle a selection mode that pauses mouse capture (where the
        # driver allows) so users can drag-select/copy transcript text. Terminals
        # that keep mouse reporting on still support Shift+drag.
        self._selection_mode = not self._selection_mode
        self._set_mouse_tracking(not self._selection_mode)
        self._refresh_status()

    def action_cancel(self) -> None:
        # Conservative Ctrl+C: clear a non-empty input; otherwise exit. Never
        # kills an in-flight tool/turn (no safe cancellation path for that yet).
        inp = self.query_one("#prompt", Input)
        if inp.value:
            inp.value = ""
        else:
            self.exit()

    def on_key(self, event: Any) -> None:
        # Up/Down navigate prompt history when the input is focused. Up only
        # starts recall on an empty line (don't clobber typed text); once
        # navigating, both keys keep walking history.
        inp = self.query_one("#prompt", Input)
        if not getattr(inp, "has_focus", False):
            return
        if event.key == "up" and (inp.value == "" or self._history.navigating):
            recalled = self._history.prev()
            if recalled is not None:
                inp.value = recalled
                inp.cursor_position = len(recalled)
                event.prevent_default()
                event.stop()
        elif event.key == "down" and self._history.navigating:
            nxt = self._history.next()
            if nxt is not None:
                inp.value = nxt
                inp.cursor_position = len(nxt)
                event.prevent_default()
                event.stop()

    async def request_approval(self, request: Any, result: Any) -> bool:
        """Approval callback for the loop: show the modal, then apply the chosen
        scope via the existing ApprovalCache. Deny on any error."""
        try:
            choice = await self.push_screen_wait(ApprovalModal(approval_view(request, result)))
        except Exception:
            return False
        if not isinstance(choice, ApprovalChoice):
            choice = ApprovalChoice.DENY
        return apply_choice(choice, self._state.approvals, request)

    # ── layout ──────────────────────────────────────────────────────
    def compose(self) -> ComposeResult:
        yield RichLog(id="transcript", wrap=True, markup=False, highlight=False)
        yield Static(self._status_text(), id="status")
        yield Input(placeholder="> prompt or /command", id="prompt")

    def _status_text(self) -> str:
        cfg = self._state.config
        try:
            from d2c.trust import get_trust_gate

            trust: Any = get_trust_gate().is_project_trusted
        except Exception:
            trust = "unknown"
        usage = ""
        session = getattr(getattr(self._state, "usage", None), "session", None)
        if session is not None and getattr(session, "calls", 0) > 0:
            from d2c.usage import usage_status_fragment

            usage = usage_status_fragment(session)
        line = status_line(
            model=cfg.model,
            mode=cfg.permission_mode,
            trust=trust,
            cwd=cfg.cwd,
            usage=usage,
            bg_tasks=self._active_bg_tasks(),
        )
        if self._selection_mode:
            line += "  |  SELECT (shift+drag to copy, ctrl+s to exit)"
        return line

    def _refresh_status(self) -> None:
        with contextlib.suppress(Exception):
            self.query_one("#status", Static).update(self._status_text())

    # ── input handling ──────────────────────────────────────────────
    async def on_input_submitted(self, event: Any) -> None:
        text = (event.value or "").strip()
        event.input.value = ""
        if not text:
            return
        self._history.add(text)
        self._write_user(text)

        if text.lower() in ("exit", "quit", "q"):
            self.exit()
            return

        from d2c.main import handle_slash_command, parse_slash_command

        cmd = parse_slash_command(text)
        if cmd is not None:
            # Route the existing print-based handlers into the transcript.
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                keep = await handle_slash_command(cmd, self._state)
            out = buf.getvalue().rstrip("\n")
            if out:
                self._write_system(out)
            self._refresh_status()
            if not keep:
                self.exit()
            return

        self.run_worker(self._consume_turn(text), exclusive=False)

    async def _consume_turn(self, text: str) -> None:
        segment = ""
        try:
            async for ev in self._run_turn(text):
                if isinstance(ev, TextDelta):
                    segment += ev.text
                elif isinstance(ev, LoopTextResponse):
                    if ev.text.strip():
                        self._write(to_renderable(ev.text.strip()))
                    segment = ""
                elif isinstance(ev, ToolExecutionEvent):
                    if segment.strip():
                        self._write(to_renderable(segment.strip()))
                    segment = ""
                    self._write_tool(ev)
                elif isinstance(ev, StopEvent):
                    if ev.reason not in ("model_finished",):
                        self._write_system(f"  [stopped: {ev.reason}]")
            if segment.strip():
                self._write(to_renderable(segment.strip()))
        except Exception as e:  # noqa: BLE001 — a turn error must not kill the app
            self._write_system(f"Error: {e}", style=_ERROR_STYLE)
        self._refresh_status()
