"""Phase 80: persisted UI preference + fallback UX.

The autouse `isolate_user_settings` fixture (conftest) points the user settings
file at a temp path, so these never touch the real ~/.d2c/settings.yaml.
"""

from __future__ import annotations

import inspect

import pytest
import yaml

import d2c.main as main
import d2c.settings as settings_mod
from d2c.config import Config
from d2c.main import ReplState, SlashCommand, handle_slash_command
from d2c.tui import (
    resolve_ui,
    set_user_ui_pref,
    ui_decision,
    user_ui_pref,
)


def _settings_path():
    # Via the module so the autouse isolate_user_settings monkeypatch applies
    # (a top-level `from ... import user_settings_path` would bind the original).
    return settings_mod.user_settings_path()


# ── precedence: CLI > env > user pref > default ─────────────────────


def test_user_pref_used_when_no_cli_or_env(monkeypatch):
    monkeypatch.delenv("D2C_TUI", raising=False)
    set_user_ui_pref("classic")
    assert resolve_ui("auto") == "classic"
    assert resolve_ui(None) == "classic"


def test_env_wins_over_user_pref(monkeypatch):
    set_user_ui_pref("classic")
    monkeypatch.setenv("D2C_TUI", "textual")
    assert resolve_ui("auto") == "textual"


def test_cli_wins_over_env_and_user_pref(monkeypatch):
    set_user_ui_pref("textual")
    monkeypatch.setenv("D2C_TUI", "textual")
    assert resolve_ui("classic") == "classic"


def test_absent_pref_falls_back_to_default(monkeypatch):
    monkeypatch.delenv("D2C_TUI", raising=False)
    assert user_ui_pref() is None
    assert resolve_ui("auto") == "textual"  # project default (Phase 79)


def test_invalid_pref_is_ignored(monkeypatch):
    monkeypatch.delenv("D2C_TUI", raising=False)
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"ui": {"default": "fancy"}}))
    assert user_ui_pref() is None
    assert resolve_ui("auto") == "textual"


def test_ui_decision_honors_pref_and_still_falls_back(monkeypatch):
    monkeypatch.delenv("D2C_TUI", raising=False)
    set_user_ui_pref("textual")
    assert ui_decision("auto", available=True) == "textual"
    assert ui_decision("auto", available=False) == "classic-fallback"
    set_user_ui_pref("classic")
    assert ui_decision("auto", available=True) == "classic"


# ── set / clear / persist ───────────────────────────────────────────


def test_set_and_clear_preference(monkeypatch):
    monkeypatch.delenv("D2C_TUI", raising=False)
    set_user_ui_pref("classic")
    assert user_ui_pref() == "classic"
    set_user_ui_pref("textual")
    assert user_ui_pref() == "textual"
    set_user_ui_pref("auto")  # clears the override
    assert user_ui_pref() is None


def test_set_pref_preserves_other_settings_keys():
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"model": "deepseek-chat"}))

    set_user_ui_pref("classic")
    data = yaml.safe_load(path.read_text())
    assert data["model"] == "deepseek-chat"  # untouched
    assert data["ui"]["default"] == "classic"

    set_user_ui_pref("auto")
    data = yaml.safe_load(path.read_text())
    assert data["model"] == "deepseek-chat"
    assert "ui" not in data  # override removed, sibling key kept


def test_set_user_ui_pref_rejects_invalid():
    with pytest.raises(ValueError):
        set_user_ui_pref("bogus")


# ── /settings ui command ────────────────────────────────────────────


def _state(cwd):
    return ReplState(config=Config(cwd=cwd), session_store=None, conversation=[])


@pytest.mark.asyncio
async def test_settings_ui_persists_classic(tmp_dir, capsys):
    cont = await handle_slash_command(
        SlashCommand(name="/settings", args=["ui", "classic"]), _state(tmp_dir)
    )
    assert cont is True
    assert "saved: classic" in capsys.readouterr().out
    assert user_ui_pref() == "classic"


@pytest.mark.asyncio
async def test_settings_ui_auto_clears(tmp_dir, capsys):
    set_user_ui_pref("classic")
    await handle_slash_command(SlashCommand(name="/settings", args=["ui", "auto"]), _state(tmp_dir))
    assert "cleared" in capsys.readouterr().out
    assert user_ui_pref() is None


@pytest.mark.asyncio
async def test_settings_ui_invalid_value(tmp_dir, capsys):
    await handle_slash_command(
        SlashCommand(name="/settings", args=["ui", "bogus"]), _state(tmp_dir)
    )
    assert "Unknown UI preference" in capsys.readouterr().out
    assert user_ui_pref() is None  # nothing persisted


@pytest.mark.asyncio
async def test_settings_ui_no_value_shows_current(tmp_dir, capsys):
    set_user_ui_pref("textual")
    await handle_slash_command(SlashCommand(name="/settings", args=["ui"]), _state(tmp_dir))
    out = capsys.readouterr().out
    assert "UI preference: textual" in out


@pytest.mark.asyncio
async def test_bare_settings_still_prints(tmp_dir, trusted_gate, capsys):
    await handle_slash_command(SlashCommand(name="/settings"), _state(tmp_dir))
    out = capsys.readouterr().out
    assert "model" in out and "permission" in out  # existing /settings output


# ── non-interactive boundary ────────────────────────────────────────


def test_headless_has_no_ui_preference_code_path():
    src = inspect.getsource(main.run_headless)
    for name in ("user_ui_pref", "set_user_ui_pref", "resolve_ui", "run_textual_app", "textual"):
        assert name not in src, f"run_headless unexpectedly references {name}"
