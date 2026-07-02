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
    "resolve_ui",
    "run_textual_app",
    "set_user_ui_pref",
    "ui_decision",
    "user_ui_pref",
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


# The project default interactive UI. Textual as of Phase 79 (the readiness
# audit in docs/textual-readiness.md passed after the Phase 78 dogfooding fixes);
# classic prompt_toolkit remains the fallback (--tui classic / D2C_TUI=classic,
# or automatically when the optional [tui] extra isn't installed).
DEFAULT_UI = "textual"


def _env_ui() -> str | None:
    """The UI requested via ``D2C_TUI`` (``textual``/``classic``), or None."""
    value = os.environ.get("D2C_TUI", "").strip().lower()
    if value in ("textual", "classic"):
        return value
    return None


def use_textual_ui() -> bool:
    """Whether the user opted into the Textual UI via ``D2C_TUI=textual``."""
    return _env_ui() == "textual"


def user_ui_pref() -> str | None:
    """The persisted personal UI preference (``ui.default`` in the USER settings
    file), as ``"classic"``/``"textual"``, or None if unset/invalid. Personal
    only — a project/managed settings file cannot force it (Phase 80)."""
    from d2c.user_prefs import get_user_pref

    value = get_user_pref("ui")
    return value.lower() if value and value.lower() in ("classic", "textual") else None


def set_user_ui_pref(value: str) -> None:
    """Persist the personal UI preference: ``classic``/``textual`` set
    ``ui.default``; ``auto`` removes it. Raises ValueError on an unknown value."""
    from d2c.user_prefs import set_user_pref

    if value not in ("classic", "textual", "auto"):
        raise ValueError(f"invalid ui preference {value!r}; expected classic|textual|auto")
    set_user_pref("ui", None if value == "auto" else value)


def resolve_ui(cli_choice: str | None = None) -> str:
    """Resolve the interactive UI to ``"classic"`` or ``"textual"``.

    Precedence (Phase 77/80): an explicit CLI ``--tui`` value wins; then
    ``D2C_TUI``; then the persisted user preference (``ui.default``); then the
    project default. ``--tui auto`` (or None) defers to env / user pref /
    default.
    """
    if cli_choice in ("classic", "textual"):
        return cli_choice
    return _env_ui() or user_ui_pref() or DEFAULT_UI


def ui_decision(cli_choice: str | None, *, available: bool) -> str:
    """Resolve to a concrete launch decision (Phase 77):

    - ``"textual"`` — launch the Textual app
    - ``"classic-fallback"`` — Textual was requested but is unavailable; fall
      back to classic and tell the user
    - ``"classic"`` — launch the classic prompt_toolkit REPL
    """
    if resolve_ui(cli_choice) == "textual":
        return "textual" if available else "classic-fallback"
    return "classic"


def is_textual_available() -> bool:
    """Whether the optional ``textual`` dependency is importable."""
    try:
        import textual  # noqa: F401

        return True
    except Exception:
        return False


async def run_textual_app(
    *, state: Any, run_turn: Any, active_bg_tasks: Any, approval_holder: Any = None
) -> None:
    """Launch the Textual app. Imports Textual lazily (callers must ensure
    :func:`is_textual_available`). ``approval_holder`` (if given) has its
    ``approval_cb`` set to the app's modal so the loop's ASK prompts render as a
    Textual modal instead of the stdin fallback."""
    from d2c.tui.app import D2CApp

    app = D2CApp(
        state=state,
        run_turn=run_turn,
        active_bg_tasks=active_bg_tasks,
        approval_holder=approval_holder,
    )
    await app.run_async()
