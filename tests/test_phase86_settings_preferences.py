"""Phase 86: /settings model/thinking/ui preferences + source reporting.

The autouse `isolate_user_settings` fixture redirects the user settings file, so
these never touch the real ~/.d2c/settings.yaml.
"""

from __future__ import annotations

import pytest
import yaml

import d2c.settings as settings_mod
from d2c.config import Config
from d2c.main import ReplState, SlashCommand, _pref_source, handle_slash_command
from d2c.user_prefs import get_user_pref, set_user_pref


def _state(cwd, **overrides):
    return ReplState(config=Config(cwd=cwd), session_store=None, conversation=[], **overrides)


async def _run(cwd, *args, **overrides):
    await handle_slash_command(
        SlashCommand(name="/settings", args=list(args)), _state(cwd, **overrides)
    )


# ── persistence via commands ────────────────────────────────────────


@pytest.mark.asyncio
async def test_settings_model_flash_and_pro(tmp_dir, capsys):
    await _run(tmp_dir, "model", "flash")
    assert get_user_pref("model") == "deepseek-v4-flash"
    assert "saved: deepseek-v4-flash" in capsys.readouterr().out

    await _run(tmp_dir, "model", "pro")
    assert get_user_pref("model") == "deepseek-v4-pro"


@pytest.mark.asyncio
async def test_settings_model_auto_clears_and_preserves_other_keys(tmp_dir):
    # seed an unrelated key + a model pref, then clear model
    path = settings_mod.user_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"ui": {"default": "classic"}}))
    set_user_pref("model", "deepseek-v4-pro")

    await _run(tmp_dir, "model", "auto")
    assert get_user_pref("model") is None
    data = yaml.safe_load(path.read_text())
    assert data["ui"]["default"] == "classic"  # unrelated key preserved


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["off", "low", "medium", "high"])
async def test_settings_thinking_presets(tmp_dir, mode):
    await _run(tmp_dir, "thinking", mode)
    assert get_user_pref("thinking") == mode


@pytest.mark.asyncio
async def test_settings_thinking_auto_clears(tmp_dir):
    set_user_pref("thinking", "medium")
    await _run(tmp_dir, "thinking", "auto")
    assert get_user_pref("thinking") is None


@pytest.mark.asyncio
async def test_invalid_values_are_nonmutating(tmp_dir, capsys):
    await _run(tmp_dir, "model", "gpt5")
    assert "Unknown model preference" in capsys.readouterr().out
    assert get_user_pref("model") is None

    await _run(tmp_dir, "thinking", "turbo")
    assert "Unknown thinking preference" in capsys.readouterr().out
    assert get_user_pref("thinking") is None


# ── config.load consumes saved prefs (env > user pref > default) ─────


def test_config_load_uses_model_and_thinking_prefs(monkeypatch):
    monkeypatch.delenv("D2C_MODEL", raising=False)
    monkeypatch.delenv("D2C_THINKING", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    set_user_pref("model", "deepseek-v4-pro")
    set_user_pref("thinking", "medium")
    cfg = Config.load()
    assert cfg.model == "deepseek-v4-pro"
    assert cfg.thinking == "medium"


def test_env_beats_saved_pref(monkeypatch):
    monkeypatch.setenv("D2C_MODEL", "deepseek-v4-flash")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    set_user_pref("model", "deepseek-v4-pro")
    assert Config.load().model == "deepseek-v4-flash"  # env wins


def test_no_pref_uses_default(monkeypatch):
    monkeypatch.delenv("D2C_MODEL", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert Config.load().model == "deepseek-v4-flash"


# ── source reporting ────────────────────────────────────────────────


def test_pref_source_default(monkeypatch):
    monkeypatch.delenv("D2C_MODEL", raising=False)
    assert (
        _pref_source("deepseek-v4-flash", env_name="D2C_MODEL", section="model", cli_value=None)
        == "deepseek-v4-flash (default)"
    )


def test_pref_source_saved(monkeypatch):
    monkeypatch.delenv("D2C_MODEL", raising=False)
    set_user_pref("model", "deepseek-v4-pro")
    s = _pref_source("deepseek-v4-pro", env_name="D2C_MODEL", section="model", cli_value=None)
    assert "saved preference" in s


def test_pref_source_env_shadows_saved(monkeypatch):
    monkeypatch.setenv("D2C_MODEL", "deepseek-v4-flash")
    set_user_pref("model", "deepseek-v4-pro")
    s = _pref_source("deepseek-v4-flash", env_name="D2C_MODEL", section="model", cli_value=None)
    assert "env: D2C_MODEL" in s
    assert "shadowed" in s


def test_pref_source_cli():
    s = _pref_source("deepseek-v4-pro", env_name="D2C_MODEL", section="model", cli_value="pro")
    assert "(CLI" in s


@pytest.mark.asyncio
async def test_bare_settings_shows_model_thinking_ui(tmp_dir, trusted_gate, capsys):
    await handle_slash_command(SlashCommand(name="/settings"), _state(tmp_dir))
    out = capsys.readouterr().out
    assert "model:" in out
    assert "thinking:" in out
    assert "ui:" in out
    assert "permission:" in out  # existing rows still present


# ── registry / autocomplete ─────────────────────────────────────────


def test_settings_subcommands_in_completion_and_help():
    from pathlib import Path

    from d2c.main import D2CCompleter, _help_lines

    cmds = D2CCompleter(Path("/tmp"), []).commands
    assert "/settings ui" in cmds
    assert "/settings model" in cmds
    assert "/settings thinking" in cmds
    help_text = "\n".join(_help_lines())
    assert "/settings" in help_text
