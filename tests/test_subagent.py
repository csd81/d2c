"""Tests for Phase 8: Subagent delegation & isolation.

Covers: SubagentDefinition, SubagentResult, build_subagent_tool_pool,
load_subagent_definition, spawn_subagent, AgentTool.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from d2c.subagent import (
    BUILTIN_SUBAGENTS,
    SubagentDefinition,
    SubagentResult,
    SubagentType,
    _parse_frontmatter,
    build_subagent_tool_pool,
    load_subagent_definition,
    spawn_subagent,
)
from d2c.tools import PermissionCategory

# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def sample_tools():
    """Create mock tools for testing tool pool building."""

    class MockTool:
        def __init__(self, name, category, concurrent=True):
            self.name = name
            self.category = category
            self.is_concurrent_safe = concurrent

    return [
        MockTool("Read", PermissionCategory.READ, True),
        MockTool("Write", PermissionCategory.WRITE, False),
        MockTool("Edit", PermissionCategory.WRITE, False),
        MockTool("Bash", PermissionCategory.SHELL, False),
        MockTool("Glob", PermissionCategory.READ, True),
        MockTool("Grep", PermissionCategory.READ, True),
        MockTool("Agent", PermissionCategory.META, True),
        MockTool("Skill", PermissionCategory.META, True),
    ]


# ── SubagentDefinition tests ──────────────────────────────────────────


class TestSubagentDefinition:
    def test_explore_definition(self):
        d = BUILTIN_SUBAGENTS["Explore"]
        assert d.name == "Explore"
        assert d.subagent_type == SubagentType.EXPLORE
        assert "Bash" in d.disallowed_tools
        assert "Write" in d.disallowed_tools
        assert "Edit" in d.disallowed_tools

    def test_plan_definition(self):
        d = BUILTIN_SUBAGENTS["Plan"]
        assert d.name == "Plan"
        assert d.subagent_type == SubagentType.PLAN
        assert d.tools is None  # no allowlist restriction

    def test_general_purpose_definition(self):
        d = BUILTIN_SUBAGENTS["General-purpose"]
        assert d.name == "General-purpose"
        assert d.subagent_type == SubagentType.GENERAL_PURPOSE
        assert d.max_turns == 25

    def test_custom_definition_fields(self):
        d = SubagentDefinition(
            name="CustomAgent",
            description="A custom agent",
            system_prompt="You are custom.",
            subagent_type=SubagentType.CUSTOM,
            tools=["Read", "Grep"],
            model="claude-haiku-4-5-20251001",
            permission_mode="dontAsk",
            max_turns=10,
            background=True,
        )
        assert d.name == "CustomAgent"
        assert d.tools == ["Read", "Grep"]
        assert d.model == "claude-haiku-4-5-20251001"
        assert d.permission_mode == "dontAsk"
        assert d.max_turns == 10
        assert d.background is True


# ── SubagentResult tests ──────────────────────────────────────────────


class TestSubagentResult:
    def test_success_result(self):
        r = SubagentResult(summary="Task done", tool_calls=3, turns=2)
        assert r.success is True
        assert r.summary == "Task done"
        assert r.tool_calls == 3
        assert r.turns == 2
        assert r.sidechain_path is None

    def test_failure_result(self):
        r = SubagentResult(summary="Error: something went wrong", success=False)
        assert r.success is False
        assert r.summary.startswith("Error")

    def test_result_with_sidechain(self):
        p = Path("/tmp/sidechain.jsonl")
        r = SubagentResult(summary="ok", sidechain_path=p)
        assert r.sidechain_path == p


# ── build_subagent_tool_pool tests ────────────────────────────────────


class TestBuildSubagentToolPool:
    def test_explore_agent_removes_write_shell(self, sample_tools):
        d = BUILTIN_SUBAGENTS["Explore"]
        pool = build_subagent_tool_pool(d, sample_tools)
        names = {t.name for t in pool}
        assert "Read" in names
        assert "Glob" in names
        assert "Grep" in names
        assert "Agent" in names
        assert "Write" not in names
        assert "Edit" not in names
        assert "Bash" not in names

    def test_plan_agent_gets_all_tools(self, sample_tools):
        d = BUILTIN_SUBAGENTS["Plan"]
        pool = build_subagent_tool_pool(d, sample_tools)
        assert len(pool) == len(sample_tools)

    def test_general_purpose_gets_all_tools(self, sample_tools):
        d = BUILTIN_SUBAGENTS["General-purpose"]
        pool = build_subagent_tool_pool(d, sample_tools)
        assert len(pool) == len(sample_tools)

    def test_allowlist_restricts_tools(self, sample_tools):
        d = SubagentDefinition(
            name="Reader",
            description="Read only",
            system_prompt="Read files.",
            subagent_type=SubagentType.CUSTOM,
            tools=["Read", "Glob"],
        )
        pool = build_subagent_tool_pool(d, sample_tools)
        names = {t.name for t in pool}
        assert names == {"Read", "Glob"}

    def test_denylist_removes_tools(self, sample_tools):
        d = SubagentDefinition(
            name="NoWrite",
            description="No write operations",
            system_prompt="Safe agent.",
            subagent_type=SubagentType.CUSTOM,
            disallowed_tools=["Bash", "Write"],
        )
        pool = build_subagent_tool_pool(d, sample_tools)
        names = {t.name for t in pool}
        assert "Bash" not in names
        assert "Write" not in names
        assert "Read" in names

    def test_allowlist_overrides_type_restrictions(self, sample_tools):
        """When allowlist is specified, type-based restrictions still apply
        but the allowlist further narrows the set."""
        d = SubagentDefinition(
            name="Hybrid",
            description="Hybrid",
            system_prompt="Hybrid.",
            subagent_type=SubagentType.EXPLORE,
            tools=["Read", "Bash"],  # allowlist, but EXPLORE filters write/shell
        )
        pool = build_subagent_tool_pool(d, sample_tools)
        names = {t.name for t in pool}
        # EXPLORE type removes non-READ/META tools, then allowlist applies
        assert "Read" in names
        assert "Bash" not in names  # filtered by EXPLORE type restriction


# ── _parse_frontmatter tests ──────────────────────────────────────────


class TestParseFrontmatter:
    def test_no_frontmatter(self):
        meta, body = _parse_frontmatter("Just body text")
        assert meta == {}
        assert body == "Just body text"

    def test_valid_frontmatter(self):
        text = """---
name: MyAgent
description: A test agent
tools:
  - Read
maxTurns: 10
---
System prompt body."""
        meta, body = _parse_frontmatter(text)
        assert meta["name"] == "MyAgent"
        assert meta["description"] == "A test agent"
        assert meta["maxTurns"] == 10
        assert body == "System prompt body."

    def test_frontmatter_boolean_values(self):
        text = """---
background: true
enabled: false
---
Body."""
        meta, body = _parse_frontmatter(text)
        assert meta["background"] is True
        assert meta["enabled"] is False

    def test_malformed_frontmatter_missing_closing(self):
        text = """---
name: Incomplete
Body."""
        meta, body = _parse_frontmatter(text)
        # Split on "---" gives only 2 parts → len < 3 → returns ({}, text)
        assert meta == {}
        assert body == text


# ── load_subagent_definition tests ────────────────────────────────────


class TestLoadSubagentDefinition:
    def test_load_explore(self):
        d = load_subagent_definition("Explore")
        assert d.subagent_type == SubagentType.EXPLORE

    def test_load_plan(self):
        d = load_subagent_definition("Plan")
        assert d.subagent_type == SubagentType.PLAN

    def test_load_general_purpose(self):
        d = load_subagent_definition("General-purpose")
        assert d.subagent_type == SubagentType.GENERAL_PURPOSE

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown subagent type"):
            load_subagent_definition("NonExistentAgent")

    def test_custom_from_file(self, tmp_path):
        agents_dir = tmp_path / ".d2c" / "agents"
        agents_dir.mkdir(parents=True)
        agent_file = agents_dir / "my-agent.md"
        agent_file.write_text("""---
name: my-agent
description: "My custom agent"
tools: "Read, Grep"
maxTurns: 15
---
You are a custom agent. Be helpful.""")

        with patch("pathlib.Path.cwd", return_value=tmp_path):
            d = load_subagent_definition("my-agent")
        assert d.name == "my-agent"
        assert d.subagent_type == SubagentType.CUSTOM
        assert d.system_prompt == "You are a custom agent. Be helpful."
        assert d.tools == "Read, Grep"
        assert d.max_turns == 15


# ── spawn_subagent tests ──────────────────────────────────────────────


class TestSpawnSubagent:
    @pytest.mark.asyncio
    async def test_spawn_subagent_runs_loop(self):
        """spawn_subagent creates isolated context and returns summary."""
        from d2c.config import Config

        config = Config.load()
        config.deepseek_api_key = "test-key"

        definition = SubagentDefinition(
            name="TestAgent",
            description="Test",
            system_prompt="You are a test agent.",
            subagent_type=SubagentType.GENERAL_PURPOSE,
            max_turns=1,
        )

        # Mock the model response to return text immediately (no tools)
        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="Task completed successfully.")]

        # anthropic is imported in d2c.loop (module-level), which spawn_subagent
        # imports via its deferred imports
        with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await spawn_subagent(
                definition=definition,
                task_prompt="Do a task.",
                parent_config=config,
                parent_session_store=None,
            )

        assert result.success is True
        assert "Task completed" in result.summary
        assert result.tool_calls == 0
        assert result.turns == 0

    @pytest.mark.asyncio
    async def test_spawn_subagent_error_handling(self):
        """spawn_subagent catches model errors gracefully in summary text."""
        from d2c.config import Config

        config = Config.load()
        config.deepseek_api_key = "test-key"

        definition = SubagentDefinition(
            name="FailingAgent",
            description="Fails",
            system_prompt="You are a failing agent.",
            subagent_type=SubagentType.GENERAL_PURPOSE,
            max_turns=1,
        )

        # Mock anthropic client to raise when messages.create is called
        # queryLoop catches this and yields TextResponse with the error,
        # so spawn_subagent returns success=True with error in summary
        with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(side_effect=RuntimeError("Connection failed"))
            mock_client_cls.return_value = mock_client

            result = await spawn_subagent(
                definition=definition,
                task_prompt="Do a task.",
                parent_config=config,
                parent_session_store=None,
            )

        assert result.success is True  # queryLoop handles errors gracefully
        assert "Connection failed" in result.summary

    @pytest.mark.asyncio
    async def test_spawn_subagent_with_session_store(self):
        """spawn_subagent creates sidechain when session store is provided."""
        import tempfile

        from d2c.config import Config
        from d2c.persistence import SessionStore

        config = Config.load()
        config.deepseek_api_key = "test-key"

        with tempfile.TemporaryDirectory() as tmpdir:
            store = SessionStore(
                base_dir=Path(tmpdir),
                session_id="parent-session",
                project_dir=Path(tmpdir),
            )

            definition = SubagentDefinition(
                name="StoreAgent",
                description="With store",
                system_prompt="You are an agent with a store.",
                subagent_type=SubagentType.GENERAL_PURPOSE,
                max_turns=1,
            )

            mock_response = MagicMock()
            mock_response.content = [MagicMock(type="text", text="Done.")]

            with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_client_cls:
                mock_client = MagicMock()
                mock_client.messages.create = AsyncMock(return_value=mock_response)
                mock_client_cls.return_value = mock_client

                result = await spawn_subagent(
                    definition=definition,
                    task_prompt="Do a task.",
                    parent_config=config,
                    parent_session_store=store,
                )

            assert result.success is True
            assert result.sidechain_path is not None


# ── AgentTool tests ───────────────────────────────────────────────────


class TestAgentTool:
    @pytest.mark.asyncio
    async def test_agent_tool_basic(self):
        """AgentTool.execute returns structured result."""
        from d2c.config import Config
        from d2c.tools.agent_tool import AgentTool

        config = Config.load()
        config.deepseek_api_key = "test-key"

        tool = AgentTool(config=config)

        # Mock spawn_subagent to return a fake result
        with patch("d2c.subagent.spawn_subagent") as mock_spawn:
            mock_spawn.return_value = SubagentResult(
                summary="I analyzed the codebase and found 3 issues.",
                tool_calls=5,
                turns=3,
                success=True,
                sidechain_path=Path("/tmp/sidechain.jsonl"),
            )

            result = await tool.execute(
                description="Analyze codebase",
                prompt="Find all bugs in the codebase.",
                subagent_type="Explore",
            )

        assert result.error is False
        assert "Explore" in result.output
        assert "I analyzed the codebase" in result.output
        assert result.metadata["tool_calls"] == 5
        assert result.metadata["turns"] == 3
        assert result.metadata["success"] is True

    @pytest.mark.asyncio
    async def test_agent_tool_unknown_subagent(self):
        """AgentTool returns error for unknown subagent type."""
        from d2c.config import Config
        from d2c.tools.agent_tool import AgentTool

        config = Config.load()
        tool = AgentTool(config=config)

        result = await tool.execute(
            description="Bad agent",
            prompt="Do something.",
            subagent_type="NonExistentAgent",
        )

        assert result.error is True
        assert "Unknown subagent" in result.output

    @pytest.mark.asyncio
    async def test_agent_tool_explore_subagent(self):
        """AgentTool with Explore subagent type."""
        from d2c.config import Config
        from d2c.tools.agent_tool import AgentTool

        config = Config.load()
        config.deepseek_api_key = "test-key"

        tool = AgentTool(config=config)

        with patch("d2c.subagent.spawn_subagent") as mock_spawn:
            mock_spawn.return_value = SubagentResult(
                summary="Found key files: main.py, config.py, loop.py",
                tool_calls=3,
                turns=2,
                success=True,
            )

            result = await tool.execute(
                description="Find key files",
                prompt="List important Python files.",
                subagent_type="Explore",
                max_turns=10,
            )

        assert result.error is False
        assert "Found key files" in result.output
        assert "Explore" in result.output
        assert result.metadata["success"] is True

    @pytest.mark.asyncio
    async def test_agent_tool_permission_mode_override(self):
        """AgentTool passes permission mode override to subagent definition."""
        from d2c.config import Config
        from d2c.tools.agent_tool import AgentTool

        config = Config.load()
        config.deepseek_api_key = "test-key"

        tool = AgentTool(config=config)

        with patch("d2c.subagent.spawn_subagent") as mock_spawn:
            mock_spawn.return_value = SubagentResult(
                summary="Done with dontAsk permissions.",
                tool_calls=1,
                turns=1,
                success=True,
            )

            result = await tool.execute(
                description="Test permissions",
                prompt="Do something.",
                subagent_type="General-purpose",
                permission_mode_override="dontAsk",
            )

        assert result.error is False
        # Verify the definition passed to spawn_subagent has the override
        call_def = mock_spawn.call_args[1]["definition"]
        assert call_def.permission_mode == "dontAsk"
