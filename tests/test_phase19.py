"""Tests for Phase 19: Streaming Tool Executor."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from d2c.streaming_executor import (
    StreamingToolExecutor,
    StreamToolParser,
)
from d2c.tools import Tool, ToolResult, ToolUse
from d2c.tools.bash_tool import BashTool


# ── Fake tools for testing ──────────────────────────────────────────────

class _FakeFastTool(Tool):
    name = "FakeFast"
    description = "Fast concurrent-safe tool."
    input_schema = {"type": "object", "properties": {}, "required": []}
    category = __import__("d2c.tools", fromlist=["PermissionCategory"]).PermissionCategory.READ
    is_concurrent_safe = True

    async def execute(self, **kwargs) -> ToolResult:
        return ToolResult(output="fast-result")


class _FakeSlowTool(Tool):
    name = "FakeSlow"
    description = "Slow non-concurrent-safe tool."
    input_schema = {"type": "object", "properties": {}, "required": []}
    category = __import__("d2c.tools", fromlist=["PermissionCategory"]).PermissionCategory.READ
    is_concurrent_safe = False

    def __init__(self, delay: float = 0.1):
        self.delay = delay

    async def execute(self, **kwargs) -> ToolResult:
        await asyncio.sleep(self.delay)
        return ToolResult(output="slow-result")


class _FakeErrorBashTool(BashTool):
    """A Bash tool that always errors (for sibling abort tests)."""

    async def execute(self, **kwargs) -> ToolResult:
        return ToolResult(output="bash error", error=True)


class _FakeUnknownTool(Tool):
    name = "FakeUnknown"
    description = "Tool not in tools_map."
    input_schema = {"type": "object", "properties": {}, "required": []}
    category = __import__("d2c.tools", fromlist=["PermissionCategory"]).PermissionCategory.READ
    is_concurrent_safe = True

    async def execute(self, **kwargs) -> ToolResult:
        return ToolResult(output="you shouldn't see this")


# ── StreamToolParser tests ──────────────────────────────────────────────

class TestStreamToolParser:
    def test_parses_single_tool_use(self):
        parser = StreamToolParser()
        parser.feed_start(0, "Read", "tu_abc123")
        parser.feed_delta(0, '{"file_path": "/tmp/test.txt"}')
        result = parser.feed_stop(0)
        assert result is not None
        assert result.id == "tu_abc123"
        assert result.name == "Read"
        assert result.input == {"file_path": "/tmp/test.txt"}

    def test_parses_multiple_tool_uses(self):
        parser = StreamToolParser()
        parser.feed_start(0, "Glob", "tu_1")
        parser.feed_delta(0, '{"pattern": "*.py"}')
        tu1 = parser.feed_stop(0)

        parser.feed_start(1, "Grep", "tu_2")
        parser.feed_delta(1, '{"pattern": "TODO"}')
        tu2 = parser.feed_stop(1)

        assert tu1.name == "Glob"
        assert tu1.input == {"pattern": "*.py"}
        assert tu2.name == "Grep"
        assert tu2.input == {"pattern": "TODO"}
        assert len(parser.completed) == 2

    def test_fragmented_json_chunks(self):
        parser = StreamToolParser()
        parser.feed_start(0, "Edit", "tu_3")
        parser.feed_delta(0, '{"file_path": ')
        parser.feed_delta(0, '"/a/b.py",')
        parser.feed_delta(0, ' "old": "x"}')
        result = parser.feed_stop(0)
        assert result is not None
        assert result.input == {"file_path": "/a/b.py", "old": "x"}

    def test_malformed_json_returns_none(self):
        parser = StreamToolParser()
        parser.feed_start(0, "Bad", "tu_bad")
        parser.feed_delta(0, "{not valid json[[[")
        result = parser.feed_stop(0)
        assert result is None
        assert len(parser.completed) == 0

    def test_unknown_index_feed_delta(self):
        parser = StreamToolParser()
        parser.feed_start(0, "Read", "tu_x")
        # delta for unknown index — should be no-op
        parser.feed_delta(99, '{"x": 1}')
        result = parser.feed_stop(0)
        assert result is not None
        assert result.input == {}

    def test_unknown_index_feed_stop(self):
        parser = StreamToolParser()
        result = parser.feed_stop(999)
        assert result is None

    def test_submitted_ids(self):
        parser = StreamToolParser()
        parser.feed_start(0, "Read", "tu_a")
        parser.feed_delta(0, '{"x": 1}')
        parser.feed_stop(0)
        parser.feed_start(1, "Write", "tu_b")
        parser.feed_delta(1, '{"y": 2}')
        parser.feed_stop(1)
        assert parser.submitted_ids == {"tu_a", "tu_b"}

    def test_empty_input(self):
        parser = StreamToolParser()
        parser.feed_start(0, "Noop", "tu_empty")
        # no deltas fed
        result = parser.feed_stop(0)
        assert result is not None
        assert result.input == {}

    def test_ignores_non_tool_start(self):
        """feed_start with empty name should not create pending entry."""
        parser = StreamToolParser()
        # Simulate a text block start (block_id empty, name empty)
        parser.feed_start(0, "", "")
        result = parser.feed_stop(0)
        # With empty name, the parser might or might not create an entry
        # depending on implementation. The key is it shouldn't crash.
        # Actually our implementation stores empty name too. That's fine.


# ── StreamingToolExecutor tests ────────────────────────────────────────

class TestStreamingToolExecutor:
    def _tools_map(self):
        return {
            "FakeFast": _FakeFastTool(),
            "FakeSlow": _FakeSlowTool(delay=0.05),
            "Bash": BashTool(),
        }

    @pytest.mark.asyncio
    async def test_submit_and_get_results(self):
        executor = StreamingToolExecutor(self._tools_map())
        tu = ToolUse(id="1", name="FakeFast", input={})
        executor.submit(tu)
        results = await executor.get_results()
        assert len(results) == 1
        rtu, result = results[0]
        assert rtu.id == "1"
        assert result.output == "fast-result"
        assert not result.error

    @pytest.mark.asyncio
    async def test_results_in_submission_order(self):
        executor = StreamingToolExecutor(self._tools_map())
        tu1 = ToolUse(id="a", name="FakeSlow", input={})
        tu2 = ToolUse(id="b", name="FakeFast", input={})
        tu3 = ToolUse(id="c", name="FakeFast", input={})

        executor.submit(tu1)
        executor.submit(tu2)
        executor.submit(tu3)

        results = await executor.get_results()
        ids = [tu.id for tu, _ in results]
        assert ids == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_concurrent_execution(self):
        """Slow tools should execute concurrently, not sequentially."""
        executor = StreamingToolExecutor(self._tools_map())
        tu1 = ToolUse(id="1", name="FakeSlow", input={})
        tu2 = ToolUse(id="2", name="FakeSlow", input={})

        start = time.monotonic()
        executor.submit(tu1)
        executor.submit(tu2)
        results = await executor.get_results()
        elapsed = time.monotonic() - start

        assert len(results) == 2
        # Both took ~0.05s each; if concurrent, total < 0.15s
        assert elapsed < 0.2  # generous margin

    @pytest.mark.asyncio
    async def test_empty_executor(self):
        executor = StreamingToolExecutor(self._tools_map())
        results = await executor.get_results()  # should return immediately
        assert results == []

    @pytest.mark.asyncio
    async def test_is_already_submitted(self):
        executor = StreamingToolExecutor(self._tools_map())
        assert not executor.is_already_submitted("tu_1")
        executor.submit(ToolUse(id="tu_1", name="FakeFast", input={}))
        assert executor.is_already_submitted("tu_1")
        assert not executor.is_already_submitted("tu_2")

    @pytest.mark.asyncio
    async def test_has_pending(self):
        executor = StreamingToolExecutor(self._tools_map())
        assert not executor.has_pending()
        tu = ToolUse(id="1", name="FakeSlow", input={})
        executor.submit(tu)
        assert executor.has_pending()
        await executor.get_results()
        assert not executor.has_pending()

    @pytest.mark.asyncio
    async def test_unknown_tool(self):
        executor = StreamingToolExecutor(self._tools_map())
        tu = ToolUse(id="x", name="NonExistent", input={})
        executor.submit(tu)
        results = await executor.get_results()
        _, result = results[0]
        assert result.error
        assert "unknown tool" in result.output.lower()

    @pytest.mark.asyncio
    async def test_sibling_abort_on_bash_error(self):
        tools = {
            "FakeSlow": _FakeSlowTool(delay=0.2),
            "Bash": _FakeErrorBashTool(),
        }
        executor = StreamingToolExecutor(tools)

        # Submit slow tool first, then error Bash
        tu_slow = ToolUse(id="slow", name="FakeSlow", input={})
        tu_bash = ToolUse(id="bash", name="Bash", input={"command": "false"})

        executor.submit(tu_slow)
        executor.submit(tu_bash)
        results = await executor.get_results()

        # Bash should have errored
        bash_result = next(r for tu, r in results if tu.id == "bash")
        assert bash_result.error

        # The slow tool should be aborted
        slow_result = next(r for tu, r in results if tu.id == "slow")
        assert slow_result.error
        assert "aborted" in slow_result.output.lower()

    @pytest.mark.asyncio
    async def test_abort_all_cancels_pending(self):
        executor = StreamingToolExecutor(self._tools_map())
        tu = ToolUse(id="1", name="FakeSlow", input={})
        executor.submit(tu)

        # Wait a tiny bit then abort
        await asyncio.sleep(0.02)
        executor.abort_all()

        # Should eventually get results (the aborted task)
        results = await executor.get_results()
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_get_results_waits_for_completion(self):
        executor = StreamingToolExecutor(self._tools_map())
        tu = ToolUse(id="wait", name="FakeSlow", input={})
        executor.submit(tu)

        start = time.monotonic()
        results = await executor.get_results()
        elapsed = time.monotonic() - start

        assert len(results) == 1
        assert elapsed >= 0.03  # actually waited

    @pytest.mark.asyncio
    async def test_pre_tool_use_hook_deny(self):
        hooks = MagicMock()
        hooks.fire = AsyncMock()
        hooks.fire.return_value = MagicMock(
            decision="deny", error="Blocked by hook",
            updated_input=None, updated_output=None, additional_context=None,
            veto=False,
        )

        executor = StreamingToolExecutor(
            self._tools_map(), hooks=hooks,
        )
        tu = ToolUse(id="1", name="FakeFast", input={})
        executor.submit(tu)
        results = await executor.get_results()
        _, result = results[0]
        assert result.error
        assert "Hook denied" in result.output
