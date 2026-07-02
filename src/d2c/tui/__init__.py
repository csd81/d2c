"""Phase 74: experimental Textual TUI for the interactive REPL.

Staged migration (the plan): the default interactive UI stays prompt_toolkit;
this package provides an opt-in Textual app (``D2C_TUI=textual``) plus the small,
Textual-free helpers it is built from (command reuse, approval-choice mapping,
Markdown rendering, status line). Importing ``d2c.tui`` never imports ``textual``
— only :func:`run_textual_app` does, lazily — so the default REPL, headless, SDK,
MCP, and eval paths are untouched.
"""

from __future__ import annotations

import os
from typing import Any, Protocol, runtime_checkable

from d2c.tui.approvals import ApprovalChoice, apply_choice, choice_from_key
from d2c.tui.commands import completion_candidates, grouped_help_lines, suggest_command
from d2c.tui.markdown import to_renderable
from d2c.tui.widgets import status_line

__all__ = [
    "ApprovalChoice",
    "InteractiveUI",
    "apply_choice",
    "choice_from_key",
    "completion_candidates",
    "grouped_help_lines",
    "is_textual_available",
    "run_textual_app",
    "status_line",
    "suggest_command",
    "to_renderable",
    "use_textual_ui",
]


@runtime_checkable
class InteractiveUI(Protocol):
    """Stage-1 boundary: the interactive UI surface the REPL depends on. The
    prompt_toolkit REPL and the Textual app are two implementations behind it."""

    async def read_prompt(self) -> str: ...

    def render_user_message(self, text: str) -> None: ...

    def render_assistant_message(self, text: str) -> None: ...

    def render_tool_event(self, event: Any) -> None: ...

    async def request_approval(self, request: Any, result: Any) -> ApprovalChoice: ...

    def render_status(self, state: Any) -> None: ...


def use_textual_ui() -> bool:
    """Whether the user opted into the Textual UI via ``D2C_TUI=textual``."""
    return os.environ.get("D2C_TUI", "").strip().lower() == "textual"


def is_textual_available() -> bool:
    """Whether the optional ``textual`` dependency is importable."""
    try:
        import textual  # noqa: F401

        return True
    except Exception:
        return False


async def run_textual_app(*, state: Any, run_turn: Any, active_bg_tasks: Any) -> None:
    """Launch the Textual app. Imports Textual lazily (callers must ensure
    :func:`is_textual_available`)."""
    from d2c.tui.app import D2CApp

    app = D2CApp(state=state, run_turn=run_turn, active_bg_tasks=active_bg_tasks)
    await app.run_async()
