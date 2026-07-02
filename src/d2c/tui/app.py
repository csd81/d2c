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
from typing import Any

from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, RichLog, Static

from d2c.loop import StopEvent, TextDelta, ToolExecutionEvent
from d2c.loop import TextResponse as LoopTextResponse
from d2c.tui.approvals import ApprovalChoice, apply_choice, approval_view, choice_from_key
from d2c.tui.markdown import to_renderable
from d2c.tui.widgets import status_line, tool_row_from_event

_MODAL_DIFF_MAX_LINES = 8  # short inline diff preview in the approval modal


class ApprovalModal(ModalScreen):
    """Phase 75: permission approval modal. Collects a choice only; the caller
    (D2CApp.request_approval) applies it via the existing approval cache, so
    approval semantics live in one place. Deny is the default (Escape/Enter/
    unknown key)."""

    CSS = """
    ApprovalModal { align: center middle; }
    #approval-box { width: 80%; max-width: 100; padding: 1 2; border: thick $warning; background: $surface; }
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
        lines.append("[y] once    [a] session    [A] always    [n] deny")
        yield Vertical(Static("\n".join(lines)), id="approval-box")

    def on_key(self, event: Any) -> None:
        if event.key == "escape":
            self.dismiss(ApprovalChoice.DENY)
            return
        self.dismiss(choice_from_key(event.character or event.key))


class D2CApp(App):
    """A minimal transcript + input + status Textual app driving the agent loop."""

    CSS = """
    #transcript { height: 1fr; border: round $panel; }
    #status { height: 1; color: $text-muted; }
    #prompt { dock: bottom; }
    """

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

    def on_mount(self) -> None:
        # Phase 75: route the agent loop's approval requests to the Textual
        # modal (instead of the prompt_toolkit/stdin fallback).
        if self._approval_holder is not None:
            self._approval_holder.approval_cb = self.request_approval

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
        return status_line(
            model=cfg.model,
            mode=cfg.permission_mode,
            trust=trust,
            cwd=cfg.cwd,
            usage=usage,
            bg_tasks=self._active_bg_tasks(),
        )

    def _refresh_status(self) -> None:
        with contextlib.suppress(Exception):
            self.query_one("#status", Static).update(self._status_text())

    # ── input handling ──────────────────────────────────────────────
    async def on_input_submitted(self, event: Any) -> None:
        text = (event.value or "").strip()
        event.input.value = ""
        if not text:
            return
        log = self.query_one("#transcript", RichLog)
        log.write(f"> {text}")

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
                log.write(out)
            self._refresh_status()
            if not keep:
                self.exit()
            return

        self.run_worker(self._consume_turn(text), exclusive=False)

    async def _consume_turn(self, text: str) -> None:
        log = self.query_one("#transcript", RichLog)
        segment = ""
        try:
            async for ev in self._run_turn(text):
                if isinstance(ev, TextDelta):
                    segment += ev.text
                elif isinstance(ev, LoopTextResponse):
                    if ev.text.strip():
                        log.write(to_renderable(ev.text.strip()))
                    segment = ""
                elif isinstance(ev, ToolExecutionEvent):
                    if segment.strip():
                        log.write(to_renderable(segment.strip()))
                    segment = ""
                    log.write(tool_row_from_event(ev))
                elif isinstance(ev, StopEvent):
                    if ev.reason not in ("model_finished",):
                        log.write(f"  [stopped: {ev.reason}]")
            if segment.strip():
                log.write(to_renderable(segment.strip()))
        except Exception as e:  # noqa: BLE001 — a turn error must not kill the app
            log.write(f"Error: {e}")
        self._refresh_status()
