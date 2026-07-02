"""Phase 82: DeepSeek thinking controls.

Config-level resolution/validation plus the request-shape plumbing (mocked
client): off sends no extra_body, enabled sends the expected budget_tokens.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import d2c.main as main
from d2c.config import VALID_THINKING_MODES, Config, thinking_budget
from d2c.loop import queryLoop
from tests.test_loop import make_loop_config, make_text_response

# ── config resolution / validation ──────────────────────────────────


def test_default_thinking_is_off(monkeypatch):
    monkeypatch.delenv("D2C_THINKING", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert Config().thinking == "off"
    assert Config.load().thinking == "off"


@pytest.mark.parametrize("mode", ["off", "low", "medium", "high"])
def test_env_override(monkeypatch, mode):
    monkeypatch.setenv("D2C_THINKING", mode)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert Config.load().thinking == mode


def test_env_value_is_normalized(monkeypatch):
    monkeypatch.setenv("D2C_THINKING", "  MEDIUM ")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert Config.load().thinking == "medium"


def test_cli_wins_over_env(monkeypatch):
    # Mirrors the run_headless/run_interactive override: `if args.thinking: ...`.
    monkeypatch.setenv("D2C_THINKING", "medium")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    config = Config.load()
    assert config.thinking == "medium"
    args_thinking = "low"
    if args_thinking:
        config.thinking = args_thinking
    assert config.thinking == "low"


def test_invalid_thinking_value_warns():
    config = Config(model="deepseek-v4-flash", thinking="turbo", deepseek_api_key="sk-test")
    warnings = [w for w in config.validate() if "Thinking mode" in w]
    assert warnings and "turbo" in warnings[0]


def test_valid_thinking_value_no_warning():
    config = Config(model="deepseek-v4-flash", thinking="high", deepseek_api_key="sk-test")
    assert not [w for w in config.validate() if "Thinking mode" in w]


def test_thinking_budget_mapping():
    assert thinking_budget("off") is None
    assert thinking_budget("low") == 4096
    assert thinking_budget("medium") == 8192
    assert thinking_budget("high") == 16384
    assert thinking_budget("garbage") is None  # unknown → off
    assert thinking_budget(None) is None
    assert VALID_THINKING_MODES == ("off", "low", "medium", "high")


# ── CLI parser ──────────────────────────────────────────────────────


def test_thinking_flag_parses(monkeypatch):
    monkeypatch.setattr("sys.argv", ["d2c", "--thinking", "high"])
    assert main.parse_args().thinking == "high"
    monkeypatch.setattr("sys.argv", ["d2c"])
    assert main.parse_args().thinking is None  # default: leave config/env


def test_invalid_thinking_flag_errors(monkeypatch):
    monkeypatch.setattr("sys.argv", ["d2c", "--thinking", "ultra"])
    with pytest.raises(SystemExit):
        main.parse_args()


# ── request-shape plumbing (mocked client) ──────────────────────────


async def _run_and_capture(thinking: str) -> dict:
    lc = make_loop_config(model="deepseek-v4-pro")
    lc.config.thinking = thinking
    captured: dict = {}

    async def _create(**kwargs):
        captured.update(kwargs)
        return make_text_response("hi")

    with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
        client = MagicMock()
        client.messages.create = AsyncMock(side_effect=_create)
        mock_cls.return_value = client
        async for _ in queryLoop(lc, [{"role": "user", "content": "hi"}]):
            pass
    return captured


@pytest.mark.asyncio
async def test_off_sends_no_thinking_payload():
    captured = await _run_and_capture("off")
    assert "extra_body" not in captured  # request shape unchanged


@pytest.mark.asyncio
async def test_medium_sends_expected_budget():
    captured = await _run_and_capture("medium")
    assert captured["extra_body"] == {"thinking": {"type": "enabled", "budget_tokens": 8192}}


@pytest.mark.asyncio
async def test_high_sends_expected_budget():
    captured = await _run_and_capture("high")
    assert captured["extra_body"]["thinking"]["budget_tokens"] == 16384
