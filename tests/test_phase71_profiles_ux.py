"""Phase 71: subagent profile UX (/profiles).

Covers listing, per-profile detail, doctor diagnostics, missing-profile
handling, and trust enforcement — against temporary .d2c/agents/ dirs and the
trust fixtures, never user home config. Verifies output avoids full instruction
bodies (only a length summary).
"""

from __future__ import annotations

import pytest

from d2c.config import Config
from d2c.main import ReplState, SlashCommand, handle_slash_command


def _write_profile(cwd, name: str, body: str) -> None:
    d = cwd / ".d2c" / "agents"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.yaml").write_text(body)


def _state(cwd) -> ReplState:
    return ReplState(config=Config(cwd=cwd), session_store=None, conversation=[])


_REFACTOR = """\
name: refactor
model: deepseek-v4-pro
permission_mode: acceptEdits
isolation: worktree
tools:
  allow: [Read, Grep, Edit, ApplyPatch]
  deny: [Bash]
instructions: |
  # Refactor helper
  Make focused, behavior-preserving edits.
"""

_PLANNER = """\
name: planner
model: deepseek-reasoner
permission_mode: plan
tools:
  allow: [Read, Grep, Glob]
"""


@pytest.mark.asyncio
async def test_profiles_lists_loaded(tmp_dir, trusted_gate, capsys):
    _write_profile(tmp_dir, "refactor", _REFACTOR)
    _write_profile(tmp_dir, "planner", _PLANNER)
    state = _state(tmp_dir)

    cont = await handle_slash_command(SlashCommand(name="/profiles"), state)
    out = capsys.readouterr().out

    assert cont is True
    assert "refactor" in out
    assert "planner" in out
    assert "deepseek-reasoner" in out
    assert "acceptEdits" in out
    assert "4 allowed" in out  # refactor's allowlist size


@pytest.mark.asyncio
async def test_profiles_show_reports_boundaries(tmp_dir, trusted_gate, capsys):
    _write_profile(tmp_dir, "refactor", _REFACTOR)
    state = _state(tmp_dir)

    await handle_slash_command(SlashCommand(name="/profiles", args=["show", "refactor"]), state)
    out = capsys.readouterr().out

    assert "name:               refactor" in out
    assert "deepseek-v4-pro" in out
    assert "acceptEdits" in out
    assert "worktree isolation: enabled" in out
    assert "ApplyPatch" in out  # allowed tools listed
    assert "denied tools:       Bash" in out
    assert "instructions:" in out
    assert "chars" in out  # summarized by length


@pytest.mark.asyncio
async def test_profiles_show_missing_is_nonmutating(tmp_dir, trusted_gate, capsys):
    _write_profile(tmp_dir, "refactor", _REFACTOR)
    state = _state(tmp_dir)

    cont = await handle_slash_command(SlashCommand(name="/profiles", args=["show", "nope"]), state)
    out = capsys.readouterr().out

    assert cont is True
    assert "Profile not found: nope" in out
    # the real profile file is untouched
    assert (tmp_dir / ".d2c" / "agents" / "refactor.yaml").exists()


@pytest.mark.asyncio
async def test_profiles_doctor_reports_invalid(tmp_dir, trusted_gate, capsys):
    _write_profile(tmp_dir, "planner", _PLANNER)
    _write_profile(tmp_dir, "broken", "name: broken\npermission_mode: bogus-mode\n")
    state = _state(tmp_dir)

    await handle_slash_command(SlashCommand(name="/profiles", args=["doctor"]), state)
    out = capsys.readouterr().out

    assert "loaded: 1" in out
    assert "skipped: 1" in out
    assert "skipped profiles:" in out
    assert "invalid permission_mode" in out
    assert "bogus-mode" in out


@pytest.mark.asyncio
async def test_profiles_untrusted_skips_project_profiles(tmp_dir, untrusted_gate, capsys):
    _write_profile(tmp_dir, "refactor", _REFACTOR)
    state = _state(tmp_dir)

    await handle_slash_command(SlashCommand(name="/profiles"), state)
    list_out = capsys.readouterr().out
    assert "untrusted" in list_out
    assert "refactor" not in list_out  # not loaded

    await handle_slash_command(SlashCommand(name="/profiles", args=["doctor"]), state)
    doctor_out = capsys.readouterr().out
    assert "loaded: 0" in doctor_out
    assert "skipped: 1" in doctor_out
    assert "refactor: project profile skipped because workspace is untrusted" in doctor_out


@pytest.mark.asyncio
async def test_profiles_show_does_not_dump_instruction_body(tmp_dir, trusted_gate, capsys):
    secret_body = "SECRET_TOKEN_abc123 do not leak this line"
    _write_profile(
        tmp_dir,
        "leaky",
        f"name: leaky\ninstructions: |\n  # Heading\n  {secret_body}\n",
    )
    state = _state(tmp_dir)

    await handle_slash_command(SlashCommand(name="/profiles", args=["show", "leaky"]), state)
    out = capsys.readouterr().out

    assert "SECRET_TOKEN_abc123" not in out
    assert "chars" in out  # length summary instead
    assert "Heading" in out  # first heading is fine to surface


@pytest.mark.asyncio
async def test_profiles_unknown_subcommand(tmp_dir, trusted_gate, capsys):
    state = _state(tmp_dir)
    cont = await handle_slash_command(SlashCommand(name="/profiles", args=["frobnicate"]), state)
    out = capsys.readouterr().out
    assert cont is True
    assert "Usage: /profiles" in out
