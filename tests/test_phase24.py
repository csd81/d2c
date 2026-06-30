"""Tests for Phase 24: Background Subagents."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from d2c.subagent import (
    BackgroundSubagentManager,
    SubagentDefinition,
    SubagentResult,
    SubagentType,
    get_background_manager,
    reset_background_manager,
)


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_bg_manager():
    """Reset the singleton background manager between tests."""
    reset_background_manager()
    yield
    reset_background_manager()


@pytest.fixture
def sample_definition():
    return SubagentDefinition(
        name="TestAgent",
        description="A test agent",
        system_prompt="You are a test agent.",
        subagent_type=SubagentType.GENERAL_PURPOSE,
    )


# ── BackgroundSubagentManager tests ───────────────────────────────────

class TestBackgroundSubagentManager:

    @pytest.mark.asyncio
    async def test_launch_returns_immediately(self, sample_definition):
        """Background subagent returns subagent_id immediately without waiting."""
        mgr = BackgroundSubagentManager()

        with patch("d2c.subagent.spawn_subagent", new_callable=AsyncMock) as mock_spawn:
            # A coroutine that would take a long time — caller returns instantly
            async def slow_spawn(*args, **kwargs):
                await asyncio.sleep(60)
                return SubagentResult(summary="done", success=True)
            mock_spawn.side_effect = slow_spawn

            subagent_id = await mgr.launch_background(
                definition=sample_definition,
                task_prompt="test task",
                parent_config=None,
            )

            # Should return with valid ID while task is still running
            assert isinstance(subagent_id, str)
            assert len(subagent_id) == 8
            assert mgr.get_status(subagent_id) == "running"

            # Cleanup
            mgr.cancel(subagent_id)

    @pytest.mark.asyncio
    async def test_status_running_and_completed(self, sample_definition):
        """Status transitions from running to completed."""
        mgr = BackgroundSubagentManager()

        with patch("d2c.subagent.spawn_subagent", new_callable=AsyncMock) as mock_spawn:
            async def quick_spawn(*args, **kwargs):
                return SubagentResult(summary="done", success=True)
            mock_spawn.side_effect = quick_spawn

            subagent_id = await mgr.launch_background(
                definition=sample_definition,
                task_prompt="test",
                parent_config=None,
            )

            # Give the task time to complete
            await asyncio.sleep(0.1)

            assert mgr.get_status(subagent_id) == "completed"
            result = mgr.get_result(subagent_id)
            assert result is not None
            assert result.summary == "done"
            assert result.success is True

    @pytest.mark.asyncio
    async def test_failure_captured(self, sample_definition):
        """Background agent failure is captured, not thrown."""
        mgr = BackgroundSubagentManager()

        with patch("d2c.subagent.spawn_subagent", new_callable=AsyncMock) as mock_spawn:
            async def failing_spawn(*args, **kwargs):
                raise ValueError("test error")
            mock_spawn.side_effect = failing_spawn

            subagent_id = await mgr.launch_background(
                definition=sample_definition,
                task_prompt="test",
                parent_config=None,
            )

            # Give the task time to fail
            await asyncio.sleep(0.1)

            assert mgr.get_status(subagent_id) == "failed"
            assert mgr.get_result(subagent_id) is None
            error = mgr.get_error(subagent_id)
            assert "test error" in error

    @pytest.mark.asyncio
    async def test_multiple_concurrent_background_agents(self, sample_definition):
        """Multiple agents can run concurrently and complete independently."""
        mgr = BackgroundSubagentManager()

        barrier = asyncio.Event()

        with patch("d2c.subagent.spawn_subagent", new_callable=AsyncMock) as mock_spawn:
            async def wait_spawn(*args, **kwargs):
                await barrier.wait()
                return SubagentResult(summary="ok", success=True)
            mock_spawn.side_effect = wait_spawn

            id1 = await mgr.launch_background(
                definition=sample_definition,
                task_prompt="task1",
                parent_config=None,
            )
            id2 = await mgr.launch_background(
                definition=sample_definition,
                task_prompt="task2",
                parent_config=None,
            )
            id3 = await mgr.launch_background(
                definition=sample_definition,
                task_prompt="task3",
                parent_config=None,
            )

            assert mgr.active_count == 3
            assert set(mgr.active_ids) == {id1, id2, id3}
            assert mgr.get_status(id1) == "running"
            assert mgr.get_status(id2) == "running"
            assert mgr.get_status(id3) == "running"

            # Release all
            barrier.set()
            await asyncio.sleep(0.1)

            assert mgr.active_count == 0
            assert mgr.get_status(id1) == "completed"
            assert mgr.get_status(id2) == "completed"
            assert mgr.get_status(id3) == "completed"

    @pytest.mark.asyncio
    async def test_cancel_running_agent(self, sample_definition):
        """Cancel stops a running background agent."""
        mgr = BackgroundSubagentManager()

        with patch("d2c.subagent.spawn_subagent", new_callable=AsyncMock) as mock_spawn:
            async def never_spawn(*args, **kwargs):
                await asyncio.sleep(600)
                return SubagentResult(summary="never", success=True)
            mock_spawn.side_effect = never_spawn

            subagent_id = await mgr.launch_background(
                definition=sample_definition,
                task_prompt="test",
                parent_config=None,
            )

            assert mgr.active_count == 1
            cancelled = mgr.cancel(subagent_id)
            assert cancelled is True

            # Second cancel should return False (already cancelled)
            cancelled2 = mgr.cancel(subagent_id)
            assert cancelled2 is False

    @pytest.mark.asyncio
    async def test_unknown_status(self):
        """Status/result/error queries for unknown IDs return sensible defaults."""
        mgr = BackgroundSubagentManager()
        assert mgr.get_status("nonexistent") == "unknown"
        assert mgr.get_result("nonexistent") is None
        assert mgr.get_error("nonexistent") is None

    @pytest.mark.asyncio
    async def test_singleton_manager(self):
        """get_background_manager returns a singleton; reset clears it."""
        mgr1 = get_background_manager()
        mgr2 = get_background_manager()
        assert mgr1 is mgr2

        reset_background_manager()
        mgr3 = get_background_manager()
        assert mgr3 is not mgr1


# ── AgentTool background integration tests ────────────────────────────

class TestAgentToolBackground:
    @pytest.mark.asyncio
    async def test_background_flag_returns_immediately(self):
        """AgentTool with background=True returns immediately with subagent ID."""
        from d2c.tools.agent_tool import AgentTool
        from d2c.config import Config

        config = Config.load()

        tool = AgentTool(config=config)

        result = await tool.execute(
            description="Background task",
            prompt="Do slow work.",
            subagent_type="General-purpose",
            background=True,
        )

        assert result.error is False
        assert "background" in result.output.lower()
        assert result.metadata["background"] is True
        assert "subagent_id" in result.metadata
        subagent_id = result.metadata["subagent_id"]
        assert isinstance(subagent_id, str)
        assert len(subagent_id) == 8

        # Verify the manager tracks it
        mgr = get_background_manager()
        assert mgr.get_status(subagent_id) in ("running", "completed", "failed")

        # Wait for it to finish (it calls real spawn_subagent which needs API key)
        # Cancel to clean up instead
        mgr.cancel(subagent_id)

    @pytest.mark.asyncio
    async def test_background_then_status_check(self):
        """Background agent can be tracked through status check flow."""
        from d2c.tools.agent_tool import AgentTool
        from d2c.config import Config

        config = Config.load()

        tool = AgentTool(config=config)

        with patch("d2c.subagent.spawn_subagent") as mock_spawn:
            async def slow_spawn(*args, **kwargs):
                await asyncio.sleep(0.5)
                return SubagentResult(summary="All done.", tool_calls=2, turns=2, success=True)
            mock_spawn.side_effect = slow_spawn

            result = await tool.execute(
                description="Tracked task",
                prompt="Do work.",
                subagent_type="Explore",
                background=True,
            )

            subagent_id = result.metadata["subagent_id"]

            mgr = get_background_manager()
            assert mgr.get_status(subagent_id) == "running"
            assert mgr.get_result(subagent_id) is None

            # Wait for completion
            await asyncio.sleep(0.6)

            assert mgr.get_status(subagent_id) == "completed"
            bg_result = mgr.get_result(subagent_id)
            assert bg_result is not None
            assert bg_result.summary == "All done."
            assert bg_result.tool_calls == 2

    @pytest.mark.asyncio
    async def test_background_failure_tracked(self):
        """Background agent failure is captured in status manager."""
        from d2c.tools.agent_tool import AgentTool
        from d2c.config import Config

        config = Config.load()

        tool = AgentTool(config=config)

        with patch("d2c.subagent.spawn_subagent") as mock_spawn:
            async def fail_spawn(*args, **kwargs):
                raise RuntimeError("Background crash")
            mock_spawn.side_effect = fail_spawn

            result = await tool.execute(
                description="Doomed task",
                prompt="Will fail.",
                subagent_type="General-purpose",
                background=True,
            )

            subagent_id = result.metadata["subagent_id"]

            mgr = get_background_manager()

            # Wait for failure
            await asyncio.sleep(0.1)

            assert mgr.get_status(subagent_id) == "failed"
            assert mgr.get_result(subagent_id) is None
            assert "Background crash" in mgr.get_error(subagent_id)
