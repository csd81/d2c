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
from textual.widgets import Input, RichLog, Static

from d2c.loop import StopEvent, TextDelta, ToolExecutionEvent
from d2c.loop import TextResponse as LoopTextResponse
from d2c.tui.markdown import to_renderable
from d2c.tui.widgets import status_line


class D2CApp(App):
    """A minimal transcript + input + status Textual app driving the agent loop."""

    CSS = """
    #transcript { height: 1fr; border: round $panel; }
    #status { height: 1; color: $text-muted; }
    #prompt { dock: bottom; }
    """

    def __init__(self, *, state: Any, run_turn: Any, active_bg_tasks: Any) -> None:
        super().__init__()
        self._state = state
        self._run_turn = run_turn
        self._active_bg_tasks = active_bg_tasks

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
                    out = ev.result.output or ""
                    suffix = "…" if len(out) > 200 else ""
                    log.write(f"  [{ev.tool_use.name}] {out[:200]}{suffix}")
                elif isinstance(ev, StopEvent):
                    if ev.reason not in ("model_finished",):
                        log.write(f"  [stopped: {ev.reason}]")
            if segment.strip():
                log.write(to_renderable(segment.strip()))
        except Exception as e:  # noqa: BLE001 — a turn error must not kill the app
            log.write(f"Error: {e}")
        self._refresh_status()
