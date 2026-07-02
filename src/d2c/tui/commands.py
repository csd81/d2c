"""Phase 74: slash-command helpers for the Textual UI.

These reuse the Phase 72 command registry in ``d2c.main`` (imported lazily to
avoid an import cycle) so the Textual UI never invents a second command system.
"""

from __future__ import annotations

import difflib
from typing import Any


def _main() -> Any:
    from d2c import main

    return main


def completion_candidates() -> list[str]:
    """Top-level commands plus common subcommands, for command completion."""
    return _main()._completion_candidates()


def grouped_help_lines() -> list[str]:
    """The same grouped /help text the prompt_toolkit REPL prints."""
    return _main()._help_lines()


def suggest_command(name: str) -> str | None:
    """Nearest known command to a mistyped one, or None if nothing is close."""
    close = difflib.get_close_matches(name, _main()._KNOWN_COMMANDS, n=1, cutoff=0.6)
    return close[0] if close else None
