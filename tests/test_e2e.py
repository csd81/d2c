"""End-to-end integration tests — full agent pipeline from config to response.

Tests the complete system: config loading, tool pool assembly, context assembly,
agent loop with realistic tool chains, session persistence, and CLI entry points.
Model responses are mocked at the API level — exercise all real code paths.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Helpers ───────────────────────────────────────────────────────────


def _make_tool_use_response(text: str, tool_uses: list[dict] | None = None):
    """Build a mock model response with optional tool_use blocks."""
    content = []
    if text:
        content.append(MagicMock(type="text", text=text))
    if tool_uses:
        for tu in tool_uses:
            block = MagicMock(type="tool_use")
            block.id = tu.get("id", "tu_1")
            block.name = tu["name"]
            block.input = tu.get("input", {})
            content.append(block)
    response = MagicMock()
    response.content = content
    return response


def _make_text_response(text: str):
    """Build a mock text-only response."""
    return _make_tool_use_response(text)


# ── Full agent loop E2E tests ─────────────────────────────────────────


class TestAgentLoopE2E:
    """End-to-end tests of the full agent loop with mocked model."""

    @pytest.mark.asyncio
    async def test_simple_question_no_tools(self):
        """User asks a question, model responds with text, no tools needed."""
        from d2c.config import Config
        from d2c.context import SystemContext, assembleMessages, getSystemPrompt, getUserContext
        from d2c.hooks import HookRegistry
        from d2c.loop import LoopConfig, StopEvent, TextResponse, queryLoop
        from d2c.permissions import PermissionEngine
        from d2c.tools.pool import Config as PoolConfig
        from d2c.tools.pool import assembleToolPool

        config = Config.load()
        config.deepseek_api_key = "test-key"

        # Assemble tools
        pool_config = PoolConfig(cwd=config.cwd)
        tools = await assembleToolPool(pool_config)

        # Build loop config
        loop_config = LoopConfig(
            system_prompt=getSystemPrompt(),
            user_context=getUserContext(config),
            model=config.model,
            max_turns=5,
            tools=tools,
            permission_engine=PermissionEngine.from_config(config),
            hooks=HookRegistry.from_config(config),
            config=config,
            deepseek_api_key="test-key",
        )

        # Assemble context
        system_context = SystemContext(
            git_status=None,
            platform="test",
            cwd="/test",
            date="2025-01-01",
        )
        full_prompt, messages = assembleMessages(
            loop_config.system_prompt,
            system_context,
            loop_config.user_context,
            [{"role": "user", "content": "What is Python?"}],
        )
        loop_config.system_prompt = full_prompt

        # Mock model: text-only response
        with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(
                return_value=_make_text_response("Python is a programming language.")
            )
            mock_cls.return_value = mock_client

            events = []
            async for event in queryLoop(loop_config, messages):
                events.append(event)

        # Should produce one TextResponse and stop
        texts = [e for e in events if isinstance(e, TextResponse)]
        stops = [e for e in events if isinstance(e, StopEvent)]
        assert len(texts) == 1
        assert "Python is a programming language" in texts[0].text
        assert len(stops) == 0  # stop is internal, not emitted as event

    @pytest.mark.asyncio
    async def test_read_tool_chain(self, tmp_path):
        """Model reads a file, processes content, returns answer."""
        from d2c.config import Config
        from d2c.context import SystemContext, assembleMessages
        from d2c.hooks import HookRegistry
        from d2c.loop import LoopConfig, TextResponse, ToolExecutionEvent, queryLoop
        from d2c.permissions import PermissionEngine, PermissionMode
        from d2c.tools.pool import Config as PoolConfig
        from d2c.tools.pool import assembleToolPool

        # Create a file to read
        test_file = tmp_path / "data.txt"
        test_file.write_text("hello world\nfoo bar\n")

        config = Config.load()
        config.deepseek_api_key = "test-key"
        config.permission_mode = "dontAsk"

        pool_config = PoolConfig(cwd=tmp_path)
        tools = await assembleToolPool(pool_config)

        loop_config = LoopConfig(
            system_prompt="You are a test agent.",
            user_context="",
            model=config.model,
            max_turns=5,
            tools=tools,
            permission_engine=PermissionEngine(
                mode=PermissionMode.DONT_ASK,
                rules=[],
            ),
            hooks=HookRegistry(),
            config=config,
            deepseek_api_key="test-key",
        )

        system_context = SystemContext(
            git_status=None,
            platform="test",
            cwd=str(tmp_path),
            date="2025-01-01",
        )
        full_prompt, messages = assembleMessages(
            loop_config.system_prompt,
            system_context,
            loop_config.user_context,
            [{"role": "user", "content": "Read data.txt and tell me what it contains."}],
        )
        loop_config.system_prompt = full_prompt

        # Multi-turn: first call returns tool_use, second returns text
        responses = [
            _make_tool_use_response(
                "Let me read that file.",
                [
                    {"id": "tu1", "name": "Read", "input": {"file_path": str(test_file)}},
                ],
            ),
            _make_text_response("The file contains: hello world, foo bar"),
        ]
        call_count = 0

        with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
            mock_client = MagicMock()

            async def side_effect(*args, **kwargs):
                nonlocal call_count
                resp = responses[min(call_count, len(responses) - 1)]
                call_count += 1
                return resp

            mock_client.messages.create = AsyncMock(side_effect=side_effect)
            mock_cls.return_value = mock_client

            events = []
            async for event in queryLoop(loop_config, messages):
                events.append(event)

        # Should have one ToolExecution + one TextResponse
        tool_events = [e for e in events if isinstance(e, ToolExecutionEvent)]
        text_events = [e for e in events if isinstance(e, TextResponse)]
        assert len(tool_events) == 1
        assert tool_events[0].tool_use.name == "Read"
        assert "hello world" in tool_events[0].result.output
        assert len(text_events) == 1
        assert "hello world" in text_events[0].text

    @pytest.mark.asyncio
    async def test_write_then_read_chain(self, tmp_path):
        """Model writes a file, then reads it back in the same session."""
        from d2c.config import Config
        from d2c.context import SystemContext, assembleMessages
        from d2c.hooks import HookRegistry
        from d2c.loop import LoopConfig, TextResponse, ToolExecutionEvent, queryLoop
        from d2c.permissions import PermissionEngine, PermissionMode
        from d2c.tools.pool import Config as PoolConfig
        from d2c.tools.pool import assembleToolPool

        output_file = tmp_path / "output.txt"

        config = Config.load()
        config.deepseek_api_key = "test-key"
        config.permission_mode = "dontAsk"

        pool_config = PoolConfig(cwd=tmp_path)
        tools = await assembleToolPool(pool_config)

        loop_config = LoopConfig(
            system_prompt="You are a test agent.",
            user_context="",
            model=config.model,
            max_turns=5,
            tools=tools,
            permission_engine=PermissionEngine(
                mode=PermissionMode.DONT_ASK,
                rules=[],
            ),
            hooks=HookRegistry(),
            config=config,
            deepseek_api_key="test-key",
        )

        system_context = SystemContext(
            git_status=None,
            platform="test",
            cwd=str(tmp_path),
            date="2025-01-01",
        )
        full_prompt, messages = assembleMessages(
            loop_config.system_prompt,
            system_context,
            loop_config.user_context,
            [{"role": "user", "content": "Write 'Hello E2E' to output.txt then verify."}],
        )
        loop_config.system_prompt = full_prompt

        # Multi-turn: write tool → read tool → text response
        responses = [
            _make_tool_use_response(
                "Writing file.",
                [
                    {
                        "id": "tu1",
                        "name": "Write",
                        "input": {
                            "file_path": str(output_file),
                            "content": "Hello E2E",
                        },
                    },
                ],
            ),
            _make_tool_use_response(
                "Now verifying.",
                [
                    {"id": "tu2", "name": "Read", "input": {"file_path": str(output_file)}},
                ],
            ),
            _make_text_response("File contains 'Hello E2E' — verified."),
        ]
        call_count = 0

        # Need to pre-read for Write tool safety check
        # File doesn't exist yet, so Write will try to create it
        # But Write tool requires Read first — so we need to handle that
        # Actually, for new files, Write should work without prior read
        # Let me check the Write tool behavior...

        with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
            mock_client = MagicMock()

            async def side_effect(*args, **kwargs):
                nonlocal call_count
                resp = responses[min(call_count, len(responses) - 1)]
                call_count += 1
                return resp

            mock_client.messages.create = AsyncMock(side_effect=side_effect)
            mock_cls.return_value = mock_client

            events = []
            async for event in queryLoop(loop_config, messages):
                events.append(event)

        tool_events = [e for e in events if isinstance(e, ToolExecutionEvent)]
        text_events = [e for e in events if isinstance(e, TextResponse)]

        assert len(tool_events) >= 2  # Write + Read
        assert len(text_events) == 1

        # Verify the file was actually written
        assert output_file.exists()
        assert output_file.read_text() == "Hello E2E"

    @pytest.mark.asyncio
    async def test_bash_tool_chain(self, tmp_path):
        """Model runs a bash command, uses output in response."""
        from d2c.config import Config
        from d2c.context import SystemContext, assembleMessages
        from d2c.hooks import HookRegistry
        from d2c.loop import LoopConfig, TextResponse, ToolExecutionEvent, queryLoop
        from d2c.permissions import PermissionEngine, PermissionMode
        from d2c.tools.pool import Config as PoolConfig
        from d2c.tools.pool import assembleToolPool

        config = Config.load()
        config.deepseek_api_key = "test-key"
        config.permission_mode = "dontAsk"

        pool_config = PoolConfig(cwd=tmp_path)
        tools = await assembleToolPool(pool_config)

        loop_config = LoopConfig(
            system_prompt="You are a test agent.",
            user_context="",
            model=config.model,
            max_turns=5,
            tools=tools,
            permission_engine=PermissionEngine(
                mode=PermissionMode.DONT_ASK,
                rules=[],
            ),
            hooks=HookRegistry(),
            config=config,
            deepseek_api_key="test-key",
        )

        system_context = SystemContext(
            git_status=None,
            platform="test",
            cwd=str(tmp_path),
            date="2025-01-01",
        )
        full_prompt, messages = assembleMessages(
            loop_config.system_prompt,
            system_context,
            loop_config.user_context,
            [{"role": "user", "content": "Run echo hello and tell me the output."}],
        )
        loop_config.system_prompt = full_prompt

        responses = [
            _make_tool_use_response(
                "Running echo.",
                [
                    {"id": "tu1", "name": "Bash", "input": {"command": "echo hello"}},
                ],
            ),
            _make_text_response("The command output was: hello"),
        ]
        call_count = 0

        with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
            mock_client = MagicMock()

            async def side_effect(*args, **kwargs):
                nonlocal call_count
                resp = responses[min(call_count, len(responses) - 1)]
                call_count += 1
                return resp

            mock_client.messages.create = AsyncMock(side_effect=side_effect)
            mock_cls.return_value = mock_client

            events = []
            async for event in queryLoop(loop_config, messages):
                events.append(event)

        tool_events = [e for e in events if isinstance(e, ToolExecutionEvent)]
        text_events = [e for e in events if isinstance(e, TextResponse)]

        assert len(tool_events) == 1
        assert tool_events[0].tool_use.name == "Bash"
        assert "hello" in tool_events[0].result.output
        assert len(text_events) == 1


# ── Config → Session → Loop integration tests ────────────────────────


class TestConfigSessionIntegration:
    """Full integration from config loading through session persistence."""

    @pytest.mark.asyncio
    async def test_config_to_loop_pipeline(self):
        """Config.load → assembleToolPool → LoopConfig → queryLoop."""
        from d2c.config import Config
        from d2c.context import SystemContext, assembleMessages, getSystemPrompt, getUserContext
        from d2c.hooks import HookRegistry
        from d2c.loop import LoopConfig, TextResponse, queryLoop
        from d2c.permissions import PermissionEngine
        from d2c.tools.pool import Config as PoolConfig
        from d2c.tools.pool import assembleToolPool

        config = Config.load()
        config.deepseek_api_key = "test-key"
        config.model = "deepseek-v4-pro"

        # Full pipeline
        pool_config = PoolConfig(cwd=config.cwd)
        tools = await assembleToolPool(pool_config)

        assert len(tools) >= 8  # All 8 base tools

        loop_config = LoopConfig(
            system_prompt=getSystemPrompt(),
            user_context=getUserContext(config),
            model=config.model,
            max_turns=5,
            tools=tools,
            permission_engine=PermissionEngine.from_config(config),
            hooks=HookRegistry.from_config(config),
            config=config,
            deepseek_api_key=config.deepseek_api_key,
            deepseek_base_url=config.deepseek_base_url,
        )

        system_context = SystemContext(
            git_status=None,
            platform="test",
            cwd="/test",
            date="2025-01-01",
        )
        full_prompt, messages = assembleMessages(
            loop_config.system_prompt,
            system_context,
            loop_config.user_context,
            [{"role": "user", "content": "Hello!"}],
        )
        loop_config.system_prompt = full_prompt

        # Verify messages include user context (CLAUDE.md if available)
        assert len(messages) >= 2  # user context + user message

        with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(
                return_value=_make_text_response("Hello! How can I help?")
            )
            mock_cls.return_value = mock_client

            events = []
            async for event in queryLoop(loop_config, messages):
                events.append(event)

        text_events = [e for e in events if isinstance(e, TextResponse)]
        assert len(text_events) == 1

    @pytest.mark.asyncio
    async def test_session_persistence_e2e(self, tmp_path):
        """Full session lifecycle: create → run loop → read transcript → resume."""
        from d2c.config import Config
        from d2c.context import SystemContext, assembleMessages
        from d2c.hooks import HookRegistry
        from d2c.loop import LoopConfig, queryLoop
        from d2c.permissions import PermissionEngine, PermissionMode
        from d2c.persistence import SessionEntry, SessionManager, _utc_now
        from d2c.tools.pool import Config as PoolConfig
        from d2c.tools.pool import assembleToolPool

        # Setup
        manager = SessionManager(base_dir=tmp_path)
        store = manager.create_session(tmp_path)

        config = Config.load()
        config.deepseek_api_key = "test-key"
        config.permission_mode = "dontAsk"

        pool_config = PoolConfig(cwd=tmp_path)
        tools = await assembleToolPool(pool_config)

        loop_config = LoopConfig(
            system_prompt="You are a test agent.",
            user_context="",
            model=config.model,
            max_turns=3,
            tools=tools,
            permission_engine=PermissionEngine(
                mode=PermissionMode.DONT_ASK,
                rules=[],
            ),
            hooks=HookRegistry(),
            config=config,
            deepseek_api_key="test-key",
            session_store=store,
        )

        system_context = SystemContext(
            git_status=None,
            platform="test",
            cwd=str(tmp_path),
            date="2025-01-01",
        )
        full_prompt, messages = assembleMessages(
            loop_config.system_prompt,
            system_context,
            loop_config.user_context,
            [{"role": "user", "content": "Test session persistence."}],
        )
        loop_config.system_prompt = full_prompt

        # Record user message (as main.py does before calling queryLoop)
        store.append(
            SessionEntry(
                role="user",
                content="Test session persistence.",
                timestamp=_utc_now(),
                entry_type="message",
            )
        )

        with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(
                return_value=_make_text_response("Session test response.")
            )
            mock_cls.return_value = mock_client

            async for event in queryLoop(loop_config, messages):
                pass

        # Verify transcript was written
        entries = store.read_transcript()
        assert len(entries) >= 3  # session_start + user + assistant

        # Resume the session
        resumed_store, resumed_messages = manager.resume_session(store.session_id, tmp_path)
        assert len(resumed_messages) >= 2  # user + assistant

        # Fork the session
        forked = manager.fork_session(store.session_id, tmp_path)
        forked_entries = forked.read_transcript()
        assert len(forked_entries) >= 2

    @pytest.mark.asyncio
    async def test_hooks_integration_e2e(self, tmp_path):
        """Verify hooks fire during the full agent loop."""
        from d2c.config import Config
        from d2c.context import SystemContext, assembleMessages
        from d2c.hooks import HookDefinition, HookEvent, HookRegistry, HookResult, HookType
        from d2c.loop import LoopConfig, queryLoop
        from d2c.permissions import PermissionEngine, PermissionMode

        test_file = tmp_path / "test.txt"
        test_file.write_text("hook test content")

        # Set up a hook that logs pre-tool-use calls
        hook_log = []

        async def log_hook(ctx):
            hook_log.append(ctx.get("tool_name"))
            return HookResult()

        hooks = HookRegistry()
        hooks.register(
            HookDefinition(
                event=HookEvent.PRE_TOOL_USE,
                hook_type=HookType.CALLBACK,
                callback=log_hook,
            )
        )

        config = Config.load()
        config.deepseek_api_key = "test-key"
        config.permission_mode = "dontAsk"

        from d2c.tools.pool import Config as PoolConfig
        from d2c.tools.pool import assembleToolPool

        pool_config = PoolConfig(cwd=tmp_path)
        tools = await assembleToolPool(pool_config)

        loop_config = LoopConfig(
            system_prompt="You are a test agent.",
            user_context="",
            model=config.model,
            max_turns=5,
            tools=tools,
            permission_engine=PermissionEngine(mode=PermissionMode.DONT_ASK, rules=[]),
            hooks=hooks,
            config=config,
            deepseek_api_key="test-key",
        )

        system_context = SystemContext(
            git_status=None,
            platform="test",
            cwd=str(tmp_path),
            date="2025-01-01",
        )
        full_prompt, messages = assembleMessages(
            loop_config.system_prompt,
            system_context,
            loop_config.user_context,
            [{"role": "user", "content": "Read test.txt"}],
        )
        loop_config.system_prompt = full_prompt

        responses = [
            _make_tool_use_response(
                "Reading.",
                [
                    {"id": "tu1", "name": "Read", "input": {"file_path": str(test_file)}},
                ],
            ),
            _make_text_response("File contains: hook test content"),
        ]
        call_count = 0

        with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
            mock_client = MagicMock()

            async def side_effect(*args, **kwargs):
                nonlocal call_count
                resp = responses[min(call_count, len(responses) - 1)]
                call_count += 1
                return resp

            mock_client.messages.create = AsyncMock(side_effect=side_effect)
            mock_cls.return_value = mock_client

            async for event in queryLoop(loop_config, messages):
                pass

        # Hook should have been called for the Read tool
        assert "Read" in hook_log


# ── CLI entry point tests ─────────────────────────────────────────────


class TestCLIEntryPoints:
    """Test the main CLI entry points with mocked model."""

    def test_list_models_flag(self, capsys):
        """--list-models should print available models."""
        import sys

        from d2c.main import main

        with patch.object(sys, "argv", ["d2c", "--list-models"]):
            try:
                main()
            except SystemExit:
                pass

        captured = capsys.readouterr()
        assert "deepseek-v4-flash" in captured.out
        assert "deepseek-v4-pro" in captured.out
        assert "[default]" in captured.out  # flash marked default
        # Phase 81: old chat/reasoner models are no longer advertised.
        assert "deepseek-chat" not in captured.out
        assert "deepseek-reasoner" not in captured.out

    @pytest.mark.asyncio
    async def test_run_headless_basic(self):
        """run_headless should execute a single prompt and return."""
        import argparse

        from d2c.main import run_headless

        args = argparse.Namespace(
            model="deepseek-v4-pro",
            max_turns=3,
            cwd=None,
            session=None,
            resume=None,
            fork=None,
        )

        with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(
                return_value=_make_text_response("E2E headless response.")
            )
            mock_cls.return_value = mock_client

            # Should not raise
            await run_headless("Test prompt", args)

    @pytest.mark.asyncio
    async def test_run_headless_read_file(self, tmp_path):
        """run_headless with a file read tool chain."""
        import argparse

        from d2c.main import run_headless

        test_file = tmp_path / "data.txt"
        test_file.write_text("e2e data")

        args = argparse.Namespace(
            model="deepseek-v4-pro",
            max_turns=5,
            cwd=tmp_path,
            session=None,
            resume=None,
            fork=None,
        )

        responses = [
            _make_tool_use_response(
                "Reading.",
                [
                    {"id": "tu1", "name": "Read", "input": {"file_path": str(test_file)}},
                ],
            ),
            _make_text_response("Found: e2e data"),
        ]
        call_count = 0

        with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
            mock_client = MagicMock()

            async def side_effect(*args, **kwargs):
                nonlocal call_count
                resp = responses[min(call_count, len(responses) - 1)]
                call_count += 1
                return resp

            mock_client.messages.create = AsyncMock(side_effect=side_effect)
            mock_cls.return_value = mock_client

            await run_headless("Read data.txt", args)


# ── Concurrent tool execution E2E tests ───────────────────────────────


class TestConcurrentToolsE2E:
    """Verify concurrent-safe tool partitioning in the full loop."""

    @pytest.mark.asyncio
    async def test_multiple_reads_parallel(self, tmp_path):
        """Multiple Read calls should execute in the same partition (parallel)."""
        from d2c.config import Config
        from d2c.context import SystemContext, assembleMessages
        from d2c.hooks import HookRegistry
        from d2c.loop import LoopConfig, ToolExecutionEvent, queryLoop
        from d2c.permissions import PermissionEngine, PermissionMode
        from d2c.tools.pool import Config as PoolConfig
        from d2c.tools.pool import assembleToolPool

        # Create multiple files
        for i in range(3):
            (tmp_path / f"file{i}.txt").write_text(f"content {i}")

        config = Config.load()
        config.deepseek_api_key = "test-key"
        config.permission_mode = "dontAsk"

        pool_config = PoolConfig(cwd=tmp_path)
        tools = await assembleToolPool(pool_config)

        loop_config = LoopConfig(
            system_prompt="Test",
            user_context="",
            model=config.model,
            max_turns=5,
            tools=tools,
            permission_engine=PermissionEngine(mode=PermissionMode.DONT_ASK, rules=[]),
            hooks=HookRegistry(),
            config=config,
            deepseek_api_key="test-key",
        )

        system_context = SystemContext(
            git_status=None,
            platform="test",
            cwd=str(tmp_path),
            date="2025-01-01",
        )
        full_prompt, messages = assembleMessages(
            loop_config.system_prompt,
            system_context,
            loop_config.user_context,
            [{"role": "user", "content": "Read all files."}],
        )
        loop_config.system_prompt = full_prompt

        # Model requests 3 reads at once
        responses = [
            _make_tool_use_response(
                "Reading all files.",
                [
                    {
                        "id": "tu1",
                        "name": "Read",
                        "input": {"file_path": str(tmp_path / "file0.txt")},
                    },
                    {
                        "id": "tu2",
                        "name": "Read",
                        "input": {"file_path": str(tmp_path / "file1.txt")},
                    },
                    {
                        "id": "tu3",
                        "name": "Read",
                        "input": {"file_path": str(tmp_path / "file2.txt")},
                    },
                ],
            ),
            _make_text_response("All files read successfully."),
        ]
        call_count = 0

        with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
            mock_client = MagicMock()

            async def side_effect(*args, **kwargs):
                nonlocal call_count
                resp = responses[min(call_count, len(responses) - 1)]
                call_count += 1
                return resp

            mock_client.messages.create = AsyncMock(side_effect=side_effect)
            mock_cls.return_value = mock_client

            events = []
            async for event in queryLoop(loop_config, messages):
                events.append(event)

        tool_events = [e for e in events if isinstance(e, ToolExecutionEvent)]
        assert len(tool_events) == 3  # All 3 reads executed

        # All reads should have succeeded
        for te in tool_events:
            assert te.result.error is False
            assert "content" in te.result.output

    @pytest.mark.asyncio
    async def test_bash_and_read_serialized(self, tmp_path):
        """Bash + Read should be in separate partitions (Bash serializes)."""
        from d2c.loop import partitionToolCalls
        from d2c.tools import ToolUse
        from d2c.tools.bash_tool import BashTool
        from d2c.tools.read_tool import FileReadTool

        tools_map = {
            "Read": FileReadTool(),
            "Bash": BashTool(cwd=tmp_path),
        }

        tool_uses = [
            ToolUse(id="1", name="Read", input={"file_path": "/tmp/a.txt"}),
            ToolUse(id="2", name="Bash", input={"command": "echo hi"}),
            ToolUse(id="3", name="Read", input={"file_path": "/tmp/b.txt"}),
        ]

        partitions = partitionToolCalls(tool_uses, tools_map)
        # Read-safe tools go in first partition, Bash in its own, remaining reads in third
        assert len(partitions) == 3
        assert len(partitions[0]) == 1  # Read
        assert partitions[1][0].name == "Bash"
        assert len(partitions[2]) == 1  # Read


# ── Error recovery E2E tests ──────────────────────────────────────────


class TestErrorRecoveryE2E:
    """End-to-end error recovery and resilience."""

    @pytest.mark.asyncio
    async def test_tool_error_recovery(self, tmp_path):
        """When a tool errors, the model gets the error and can retry."""
        from d2c.config import Config
        from d2c.context import SystemContext, assembleMessages
        from d2c.hooks import HookRegistry
        from d2c.loop import LoopConfig, TextResponse, ToolExecutionEvent, queryLoop
        from d2c.permissions import PermissionEngine, PermissionMode
        from d2c.tools.pool import Config as PoolConfig
        from d2c.tools.pool import assembleToolPool

        config = Config.load()
        config.deepseek_api_key = "test-key"
        config.permission_mode = "dontAsk"

        pool_config = PoolConfig(cwd=tmp_path)
        tools = await assembleToolPool(pool_config)

        loop_config = LoopConfig(
            system_prompt="Test",
            user_context="",
            model=config.model,
            max_turns=5,
            tools=tools,
            permission_engine=PermissionEngine(mode=PermissionMode.DONT_ASK, rules=[]),
            hooks=HookRegistry(),
            config=config,
            deepseek_api_key="test-key",
        )

        system_context = SystemContext(
            git_status=None,
            platform="test",
            cwd=str(tmp_path),
            date="2025-01-01",
        )
        full_prompt, messages = assembleMessages(
            loop_config.system_prompt,
            system_context,
            loop_config.user_context,
            [{"role": "user", "content": "Read a missing file."}],
        )
        loop_config.system_prompt = full_prompt

        # Model tries to read nonexistent file, then recovers
        nonexistent = str(tmp_path / "does_not_exist.txt")
        responses = [
            _make_tool_use_response(
                "Attempting read.",
                [
                    {"id": "tu1", "name": "Read", "input": {"file_path": nonexistent}},
                ],
            ),
            _make_text_response("The file does not exist. Let me create it instead."),
        ]
        call_count = 0

        with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
            mock_client = MagicMock()

            async def side_effect(*args, **kwargs):
                nonlocal call_count
                resp = responses[min(call_count, len(responses) - 1)]
                call_count += 1
                return resp

            mock_client.messages.create = AsyncMock(side_effect=side_effect)
            mock_cls.return_value = mock_client

            events = []
            async for event in queryLoop(loop_config, messages):
                events.append(event)

        tool_events = [e for e in events if isinstance(e, ToolExecutionEvent)]
        text_events = [e for e in events if isinstance(e, TextResponse)]

        # Read should have errored
        assert len(tool_events) == 1
        assert tool_events[0].result.error is True
        # Model should have responded to the error
        assert len(text_events) == 1
        assert (
            "does not exist" in text_events[0].text.lower()
            or "create" in text_events[0].text.lower()
        )

    @pytest.mark.asyncio
    async def test_max_turns_enforced(self, tmp_path):
        """Loop should stop when max_turns is reached."""
        from d2c.config import Config
        from d2c.context import SystemContext, assembleMessages
        from d2c.hooks import HookRegistry
        from d2c.loop import LoopConfig, StopEvent, queryLoop
        from d2c.permissions import PermissionEngine, PermissionMode
        from d2c.tools.pool import Config as PoolConfig
        from d2c.tools.pool import assembleToolPool

        config = Config.load()
        config.deepseek_api_key = "test-key"
        config.permission_mode = "dontAsk"

        pool_config = PoolConfig(cwd=tmp_path)
        tools = await assembleToolPool(pool_config)

        loop_config = LoopConfig(
            system_prompt="Test",
            user_context="",
            model=config.model,
            max_turns=2,  # Very low limit
            tools=tools,
            permission_engine=PermissionEngine(mode=PermissionMode.DONT_ASK, rules=[]),
            hooks=HookRegistry(),
            config=config,
            deepseek_api_key="test-key",
        )

        system_context = SystemContext(
            git_status=None,
            platform="test",
            cwd=str(tmp_path),
            date="2025-01-01",
        )
        full_prompt, messages = assembleMessages(
            loop_config.system_prompt,
            system_context,
            loop_config.user_context,
            [{"role": "user", "content": "Keep going."}],
        )
        loop_config.system_prompt = full_prompt

        # Always return tool_use to force turns
        test_file = tmp_path / "loop.txt"
        test_file.write_text("data")

        with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(
                return_value=_make_tool_use_response(
                    "Reading.",
                    [
                        {"id": "tu_loop", "name": "Read", "input": {"file_path": str(test_file)}},
                    ],
                )
            )
            mock_cls.return_value = mock_client

            events = []
            async for event in queryLoop(loop_config, messages):
                events.append(event)

        # Should have stopped due to max_turns
        stop_events = [e for e in events if isinstance(e, StopEvent)]
        assert any(e.reason == "max_turns" for e in stop_events)
