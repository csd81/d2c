"""Tests for Phase 10: DeepSeek wiring — config, model mapping, streaming, validation."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── .env loading tests ─────────────────────────────────────────────────


class TestEnvLoading:
    def test_parse_simple_key_value(self, tmp_path, monkeypatch):
        """Basic KEY=VALUE parsing."""
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

        env_file = tmp_path / ".env"
        env_file.write_text("DEEPSEEK_API_KEY=sk-test-12345\n")

        from d2c.config import _parse_env_file

        _parse_env_file(env_file)
        assert os.environ["DEEPSEEK_API_KEY"] == "sk-test-12345"

    def test_parse_quoted_value(self, tmp_path, monkeypatch):
        """Quoted values should have quotes stripped."""
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

        env_file = tmp_path / ".env"
        env_file.write_text('DEEPSEEK_API_KEY="sk-test-abc"\n')

        from d2c.config import _parse_env_file

        _parse_env_file(env_file)
        assert os.environ["DEEPSEEK_API_KEY"] == "sk-test-abc"

    def test_parse_single_quoted_value(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

        env_file = tmp_path / ".env"
        env_file.write_text("DEEPSEEK_API_KEY='sk-test-xyz'\n")

        from d2c.config import _parse_env_file

        _parse_env_file(env_file)
        assert os.environ["DEEPSEEK_API_KEY"] == "sk-test-xyz"

    def test_parse_skips_comments(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

        env_file = tmp_path / ".env"
        env_file.write_text("# This is a comment\nDEEPSEEK_API_KEY=sk-key\n")

        from d2c.config import _parse_env_file

        _parse_env_file(env_file)
        assert os.environ["DEEPSEEK_API_KEY"] == "sk-key"

    def test_parse_skips_blank_lines(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

        env_file = tmp_path / ".env"
        env_file.write_text("\n\nDEEPSEEK_API_KEY=sk-key\n\n")

        from d2c.config import _parse_env_file

        _parse_env_file(env_file)
        assert os.environ["DEEPSEEK_API_KEY"] == "sk-key"

    def test_parse_export_keyword(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

        env_file = tmp_path / ".env"
        env_file.write_text("export DEEPSEEK_API_KEY=sk-exported\n")

        from d2c.config import _parse_env_file

        _parse_env_file(env_file)
        assert os.environ["DEEPSEEK_API_KEY"] == "sk-exported"

    def test_does_not_override_existing_env(self, tmp_path, monkeypatch):
        """Existing env vars take precedence over .env."""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-shell")

        env_file = tmp_path / ".env"
        env_file.write_text("DEEPSEEK_API_KEY=sk-from-file\n")

        from d2c.config import _parse_env_file

        _parse_env_file(env_file)
        assert os.environ["DEEPSEEK_API_KEY"] == "sk-from-shell"

    def test_load_dotenv_no_files(self, tmp_path):
        """No .env files — does nothing, no error."""
        from d2c.config import _load_project_dotenv

        _load_project_dotenv(tmp_path)  # Should not raise

    def test_load_dotenv_reads_project_env(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

        env_file = tmp_path / ".env"
        env_file.write_text("DEEPSEEK_API_KEY=sk-project\n")

        from d2c.config import _load_project_dotenv

        _load_project_dotenv(tmp_path)
        assert os.environ["DEEPSEEK_API_KEY"] == "sk-project"


# ── Model mapping tests ────────────────────────────────────────────────


class TestModelMapping:
    def test_resolve_v4_pro(self):
        from d2c.config import resolve_model

        assert resolve_model("v4-pro") == "deepseek-v4-pro"
        assert resolve_model("v4") == "deepseek-v4-pro"
        assert resolve_model("deepseek-v4-pro") == "deepseek-v4-pro"

    def test_resolve_flash(self):
        from d2c.config import resolve_model

        assert resolve_model("flash") == "deepseek-v4-flash"
        assert resolve_model("v4-flash") == "deepseek-v4-flash"
        assert resolve_model("deepseek-v4-flash") == "deepseek-v4-flash"

    def test_resolve_pro(self):
        from d2c.config import resolve_model

        assert resolve_model("pro") == "deepseek-v4-pro"
        assert resolve_model("v4") == "deepseek-v4-pro"

    def test_resolve_case_insensitive(self):
        from d2c.config import resolve_model

        assert resolve_model("V4-PRO") == "deepseek-v4-pro"
        assert resolve_model("Flash") == "deepseek-v4-flash"

    def test_removed_aliases_pass_through_as_custom(self):
        # Phase 81: chat/reasoner aliases removed; unknown strings pass through
        # unchanged (advanced users can still pass a raw model ID).
        from d2c.config import resolve_model

        assert resolve_model("chat") == "chat"
        assert resolve_model("reasoner") == "reasoner"
        assert resolve_model("deepseek-chat") == "deepseek-chat"

    def test_resolve_unknown_passthrough(self):
        from d2c.config import resolve_model

        assert resolve_model("some-custom-model") == "some-custom-model"

    def test_get_model_defaults_known(self):
        from d2c.config import get_model_defaults

        defaults = get_model_defaults("v4-pro")
        assert "max_tokens" in defaults
        assert "context_window" in defaults

    def test_get_model_defaults_unknown(self):
        from d2c.config import get_model_defaults

        defaults = get_model_defaults("unknown-model")
        assert defaults["max_tokens"] == 8192


# ── Config validation tests ────────────────────────────────────────────


class TestConfigValidate:
    def test_validates_missing_api_key(self):
        from d2c.config import Config

        config = Config(deepseek_api_key=None)
        issues = config.validate()
        assert len(issues) > 0
        assert any("DEEPSEEK_API_KEY" in i for i in issues)

    def test_validates_unknown_model(self):
        from d2c.config import Config

        config = Config(model="nonexistent-model", deepseek_api_key="sk-test")
        issues = config.validate()
        has_model_warning = any("not a recognized" in i for i in issues)
        assert has_model_warning

    def test_validates_known_model_no_warning(self):
        from d2c.config import Config

        config = Config(model="deepseek-v4-flash", deepseek_api_key="sk-test")
        issues = config.validate()
        model_warnings = [i for i in issues if "not a recognized" in i]
        assert len(model_warnings) == 0

    def test_valid_config_no_issues(self):
        from d2c.config import Config

        config = Config(model="deepseek-v4-pro", deepseek_api_key="sk-test")
        issues = config.validate()
        assert issues == []

    def test_model_aliases_resolved_in_post_init(self):
        from d2c.config import Config

        config = Config(model="v4-pro", deepseek_api_key="sk-test")
        assert config.model == "deepseek-v4-pro"

    def test_context_window_defaults_applied(self):
        from d2c.config import Config

        config = Config(model="deepseek-chat")
        assert config.context_window_tokens == 128_000


# ── Config.load tests ──────────────────────────────────────────────────


class TestConfigLoad:
    def test_load_from_env(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-env")
        monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/anthropic")
        monkeypatch.setenv("D2C_MODEL", "deepseek-chat")

        from d2c.config import Config

        config = Config.load()
        assert config.deepseek_api_key == "sk-from-env"
        assert config.deepseek_base_url == "https://api.deepseek.com/anthropic"
        assert config.model == "deepseek-chat"

    def test_load_defaults(self, monkeypatch):
        # Ensure no env vars set
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
        monkeypatch.delenv("D2C_MODEL", raising=False)

        from d2c.config import Config

        config = Config.load()
        assert config.model == "deepseek-v4-flash"
        assert config.deepseek_base_url == "https://api.deepseek.com/anthropic"

    def test_load_from_dotenv(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("D2C_MODEL", raising=False)

        env_file = tmp_path / ".env"
        env_file.write_text("DEEPSEEK_API_KEY=sk-from-dotenv\nD2C_MODEL=deepseek-chat\n")

        from d2c.config import Config

        config = Config.load(cwd=tmp_path)
        assert config.deepseek_api_key == "sk-from-dotenv"
        assert config.model == "deepseek-chat"

        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("D2C_MODEL", raising=False)


# ── TextDelta tests ────────────────────────────────────────────────────


class TestTextDelta:
    def test_text_delta_creation(self):
        from d2c.loop import TextDelta

        delta = TextDelta(text="Hello", first=True)
        assert delta.text == "Hello"
        assert delta.first is True

    def test_text_delta_defaults(self):
        from d2c.loop import TextDelta

        delta = TextDelta(text="world")
        assert delta.first is False


# ── Streaming behavior tests ───────────────────────────────────────────


class TestStreaming:
    @pytest.mark.asyncio
    async def test_loop_stream_disabled_uses_create(self):
        """When stream=False, should use client.messages.create."""
        from d2c.config import Config
        from d2c.hooks import HookRegistry
        from d2c.loop import LoopConfig, TextResponse, queryLoop
        from d2c.permissions import PermissionEngine

        config = Config.load()
        config.deepseek_api_key = "test-key"
        config.model = "deepseek-v4-pro"

        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="Hello")]

        loop_config = LoopConfig(
            system_prompt="test",
            user_context="",
            model=config.model,
            max_turns=1,
            tools=[],
            permission_engine=PermissionEngine.from_config(config),
            hooks=HookRegistry(),
            config=config,
            deepseek_api_key="test-key",
            stream=False,
        )

        with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            mock_cls.return_value = mock_client

            events = []
            async for event in queryLoop(loop_config, [{"role": "user", "content": "hi"}]):
                events.append(event)

        # Should have called create, not stream
        mock_client.messages.create.assert_called_once()
        assert any(isinstance(e, TextResponse) for e in events)

    @pytest.mark.asyncio
    async def test_loop_stream_enabled_uses_stream(self):
        """When stream=True, should use client.messages.stream."""
        from d2c.config import Config
        from d2c.hooks import HookRegistry
        from d2c.loop import LoopConfig, TextDelta, TextResponse, queryLoop
        from d2c.permissions import PermissionEngine

        config = Config.load()
        config.deepseek_api_key = "test-key"

        # Simulated stream events — use real classes the anthropic SDK would emit
        class TextEvent:
            type = "text_delta"
            text = "Hello, "

        class TextEvent2:
            type = "text_delta"
            text = "world!"

        stream_events = [TextEvent(), TextEvent2()]

        # Build an async iterator wrapper
        class AsyncStreamIterator:
            def __init__(self, events):
                self._events = events
                self._index = 0

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._index >= len(self._events):
                    raise StopAsyncIteration
                event = self._events[self._index]
                self._index += 1
                return event

        # Build a mock stream context manager
        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=AsyncStreamIterator(stream_events))
        mock_stream.__aexit__ = AsyncMock(return_value=None)

        # Final message from the stream
        mock_final = MagicMock()
        mock_final.content = [MagicMock(type="text", text="Hello, world!")]
        mock_stream.get_final_message = AsyncMock(return_value=mock_final)

        loop_config = LoopConfig(
            system_prompt="test",
            user_context="",
            model=config.model,
            max_turns=1,
            tools=[],
            permission_engine=PermissionEngine.from_config(config),
            hooks=HookRegistry(),
            config=config,
            deepseek_api_key="test-key",
            stream=True,
        )

        with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
            mock_client = MagicMock()
            mock_client.messages.stream = MagicMock(return_value=mock_stream)
            mock_cls.return_value = mock_client

            events = []
            async for event in queryLoop(loop_config, [{"role": "user", "content": "hi"}]):
                events.append(event)

        # Should yield TextDelta events for streaming chunks
        text_deltas = [e for e in events if isinstance(e, TextDelta)]
        assert len(text_deltas) == 2
        assert text_deltas[0].text == "Hello, "
        assert text_deltas[1].text == "world!"

        # Final TextResponse should also be yielded
        text_responses = [e for e in events if isinstance(e, TextResponse)]
        assert len(text_responses) == 1
        assert text_responses[0].text == "Hello, world!"

    @pytest.mark.asyncio
    async def test_loop_auth_error_handling(self):
        """AuthenticationError should yield clear error message."""
        import anthropic

        from d2c.config import Config
        from d2c.hooks import HookRegistry
        from d2c.loop import LoopConfig, TextResponse, queryLoop
        from d2c.permissions import PermissionEngine

        config = Config.load()
        config.deepseek_api_key = "bad-key"

        loop_config = LoopConfig(
            system_prompt="test",
            user_context="",
            model=config.model,
            max_turns=1,
            tools=[],
            permission_engine=PermissionEngine.from_config(config),
            hooks=HookRegistry(),
            config=config,
            deepseek_api_key="bad-key",
            stream=False,
        )

        # Construct a proper AuthenticationError
        mock_response = MagicMock()
        mock_response.status_code = 401
        auth_error = anthropic.AuthenticationError(
            "Invalid API key",
            response=mock_response,
            body={"error": {"message": "Invalid API key"}},
        )

        with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(side_effect=auth_error)
            mock_cls.return_value = mock_client

            events = []
            async for event in queryLoop(loop_config, [{"role": "user", "content": "hi"}]):
                events.append(event)

        text_events = [e for e in events if isinstance(e, TextResponse)]
        assert len(text_events) >= 1
        assert "authentication failed" in text_events[0].text.lower()
        assert "401" in text_events[0].text
        assert "DEEPSEEK_API_KEY" in text_events[0].text

    @pytest.mark.asyncio
    async def test_loop_rate_limit_handling(self):
        """RateLimitError should yield clear error message."""
        import anthropic

        from d2c.config import Config
        from d2c.hooks import HookRegistry
        from d2c.loop import LoopConfig, TextResponse, queryLoop
        from d2c.permissions import PermissionEngine

        config = Config.load()
        config.deepseek_api_key = "test-key"

        loop_config = LoopConfig(
            system_prompt="test",
            user_context="",
            model=config.model,
            max_turns=1,
            tools=[],
            permission_engine=PermissionEngine.from_config(config),
            hooks=HookRegistry(),
            config=config,
            deepseek_api_key="test-key",
            stream=False,
        )

        # Construct a proper RateLimitError
        mock_response = MagicMock()
        mock_response.status_code = 429
        rate_error = anthropic.RateLimitError(
            "Too many requests",
            response=mock_response,
            body={"error": {"message": "Too many requests"}},
        )

        with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(side_effect=rate_error)
            mock_cls.return_value = mock_client

            events = []
            async for event in queryLoop(loop_config, [{"role": "user", "content": "hi"}]):
                events.append(event)

        text_events = [e for e in events if isinstance(e, TextResponse)]
        assert len(text_events) >= 1
        assert "rate-limiting" in text_events[0].text.lower()
        assert "429" in text_events[0].text
