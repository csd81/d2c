"""Phase 72: REPL command UX consolidation.

Covers grouped /help rendered from the shared command registry, autocomplete
coverage of top-level commands and common subcommands, and typo suggestions for
unknown commands.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from d2c.config import Config
from d2c.main import (
    _COMMAND_SPECS,
    D2CCompleter,
    ReplState,
    SlashCommand,
    handle_slash_command,
)


def _state(tmp_dir) -> ReplState:
    return ReplState(config=Config(cwd=tmp_dir), session_store=None, conversation=[])


def _doc(text: str) -> SimpleNamespace:
    return SimpleNamespace(text_before_cursor=text, text=text)


# ── /help ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_help_lists_every_top_level_command(tmp_dir, capsys):
    await handle_slash_command(SlashCommand(name="/help"), _state(tmp_dir))
    out = capsys.readouterr().out
    for spec in _COMMAND_SPECS:
        assert spec.name in out, f"{spec.name} missing from /help"


@pytest.mark.asyncio
async def test_help_is_grouped_by_workflow(tmp_dir, capsys):
    await handle_slash_command(SlashCommand(name="/help"), _state(tmp_dir))
    out = capsys.readouterr().out
    for heading in ("Session", "State", "Safety", "Help"):
        assert heading in out
    # Session group appears before Safety group.
    assert out.index("Session") < out.index("Safety")


# ── Autocomplete ────────────────────────────────────────────────────


def test_completer_covers_all_top_level_commands():
    c = D2CCompleter(Path("/tmp"), [])
    for spec in _COMMAND_SPECS:
        assert spec.name in c.commands
    assert "/quit" in c.commands  # alias still completes


def test_completer_covers_common_subcommands():
    c = D2CCompleter(Path("/tmp"), [])
    for combo in (
        "/approvals clear-session",
        "/approvals reset",
        "/profiles show",
        "/profiles doctor",
    ):
        assert combo in c.commands


def test_completer_yields_subcommands_for_prefix():
    c = D2CCompleter(Path("/tmp"), [])
    names = {comp.text for comp in c.get_completions(_doc("/approvals "), None)}
    assert "/approvals clear-session" in names
    assert "/approvals reset" in names


# ── Unknown-command suggestions ─────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_typo_suggests_nearest(tmp_dir, capsys):
    cont = await handle_slash_command(SlashCommand(name="/aprovals"), _state(tmp_dir))
    out = capsys.readouterr().out
    assert cont is True
    assert "Unknown command: /aprovals" in out
    assert "Did you mean /approvals?" in out


@pytest.mark.asyncio
async def test_unknown_unrelated_points_to_help(tmp_dir, capsys):
    cont = await handle_slash_command(SlashCommand(name="/whatever"), _state(tmp_dir))
    out = capsys.readouterr().out
    assert cont is True
    assert "Unknown command: /whatever" in out
    assert "Run /help to see available commands." in out
