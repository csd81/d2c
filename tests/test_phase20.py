"""Tests for Phase 20: Deferred Tool Schemas."""

from __future__ import annotations

import asyncio

import pytest

from d2c.tools.pool import Config, assembleToolPool, getAllBaseTools
from d2c.tools.tool_search import DeferredToolSchema, ToolSearchTool

# ── DeferredToolSchema tests ────────────────────────────────────────────


class TestDeferredToolSchema:
    def test_deferred_shows_only_name(self):
        """Deferred tool shows abbreviated schema, not full one."""
        from d2c.tools.bash_tool import BashTool

        tool = BashTool()
        deferred = DeferredToolSchema(tool)

        api = deferred.to_api_format()
        assert api["name"] == "Bash"
        assert "Schema deferred" in api["description"]
        # Input schema should be empty (abbreviated)
        assert api["input_schema"]["properties"] == {}

    def test_loaded_returns_full_schema(self):
        """After loading, returns full schema from wrapped tool."""
        from d2c.tools.read_tool import FileReadTool

        tool = FileReadTool()
        deferred = DeferredToolSchema(tool)

        # Before load
        api_before = deferred.to_api_format()
        assert api_before["input_schema"]["properties"] == {}

        # After load
        deferred.load_full_schema()
        api_after = deferred.to_api_format()
        assert api_after["input_schema"]["properties"] != {}

    def test_is_schema_loaded(self):
        from d2c.tools.glob_tool import GlobTool

        deferred = DeferredToolSchema(GlobTool())
        assert not deferred.is_schema_loaded
        deferred.load_full_schema()
        assert deferred.is_schema_loaded

    def test_double_load_is_idempotent(self):
        from d2c.tools.glob_tool import GlobTool

        deferred = DeferredToolSchema(GlobTool())
        deferred.load_full_schema()
        deferred.load_full_schema()  # should not raise
        assert deferred.is_schema_loaded

    def test_name_property(self):
        from d2c.tools.edit_tool import FileEditTool

        deferred = DeferredToolSchema(FileEditTool())
        assert deferred.name == "Edit"


# ── ToolSearchTool tests ────────────────────────────────────────────────


class TestToolSearchTool:
    def _make_registry(self):
        from d2c.tools.edit_tool import FileEditTool
        from d2c.tools.glob_tool import GlobTool
        from d2c.tools.grep_tool import GrepTool
        from d2c.tools.read_tool import FileReadTool
        from d2c.tools.write_tool import FileWriteTool

        return [
            FileReadTool(),
            DeferredToolSchema(FileWriteTool()),
            DeferredToolSchema(FileEditTool()),
            DeferredToolSchema(GlobTool()),
            DeferredToolSchema(GrepTool()),
        ]

    def test_select_exact_names(self):
        registry = self._make_registry()
        tool = ToolSearchTool(registry)

        result = asyncio.run(tool.execute(query="select:Write,Edit", max_results=5))
        assert not result.error
        assert "Write" in result.output
        assert "Edit" in result.output
        assert result.metadata["matched"] == ["Write", "Edit"]

    def test_select_loads_schemas(self):
        registry = self._make_registry()
        tool = ToolSearchTool(registry)

        asyncio.run(tool.execute(query="select:Write,Glob", max_results=5))

        # Only Write and Glob should be loaded, not other deferred tools
        for entry in registry:
            if isinstance(entry, DeferredToolSchema):
                if entry.name in ("Write", "Glob"):
                    assert entry.is_schema_loaded
                else:
                    assert not entry.is_schema_loaded

    def test_keyword_search(self):
        registry = self._make_registry()
        tool = ToolSearchTool(registry)

        result = asyncio.run(tool.execute(query="Read", max_results=5))
        assert not result.error
        # FileReadTool should match "Read"
        assert result.metadata["count"] >= 1
        assert "Read" in result.output

    def test_keyword_search_loads_matching(self):
        registry = self._make_registry()
        tool = ToolSearchTool(registry)

        asyncio.run(tool.execute(query="grep", max_results=5))

        # Only GrepTool should be loaded
        for entry in registry:
            if isinstance(entry, DeferredToolSchema):
                if entry.name == "Grep":
                    assert entry.is_schema_loaded
                else:
                    assert not entry.is_schema_loaded

    def test_no_match_returns_helpful_message(self):
        registry = self._make_registry()
        tool = ToolSearchTool(registry)

        result = asyncio.run(tool.execute(query="zzz_nonexistent_zzz", max_results=5))
        assert not result.error
        assert "No tools found" in result.output

    def test_max_results_limit(self):
        registry = self._make_registry()
        tool = ToolSearchTool(registry)

        result = asyncio.run(tool.execute(query="e", max_results=2))
        assert result.metadata["count"] <= 2

    def test_select_nonexistent(self):
        registry = self._make_registry()
        tool = ToolSearchTool(registry)

        result = asyncio.run(tool.execute(query="select:Nope,AlsoNope", max_results=5))
        assert not result.error
        assert "No tools found" in result.output


# ── Pool integration tests ─────────────────────────────────────────────


class TestDeferredPoolIntegration:
    def test_deferred_disabled_by_default(self):
        """Without deferred_tools, all tools have full schemas."""
        config = Config()
        tools = getAllBaseTools(config)
        schemas = [t.to_api_format() for t in tools]
        # Some tools have input params
        has_props = any(len(s.get("input_schema", {}).get("properties", {})) > 0 for s in schemas)
        assert has_props

    def test_deferred_enabled_wraps_large_schemas(self):
        """With deferred_tools=True, large-schema tools get abbreviated."""
        config = Config(deferred_tools=True)
        tools = getAllBaseTools(config)

        deferred_count = sum(1 for t in tools if isinstance(t, DeferredToolSchema))
        assert deferred_count > 0  # At least some tools are deferred

        # Deferred tools have empty properties
        for t in tools:
            if isinstance(t, DeferredToolSchema):
                api = t.to_api_format()
                assert api["input_schema"]["properties"] == {}

    def test_deferred_mode_includes_tool_search(self):
        """ToolSearchTool is always included, even in deferred mode."""
        config = Config(deferred_tools=True)
        tools = getAllBaseTools(config)
        names = {t.name for t in tools}
        assert "ToolSearch" in names

    @pytest.mark.asyncio
    async def test_assemble_pool_with_deferred(self):
        """assembleToolPool works with deferred_tools and wires registry."""
        config = Config(deferred_tools=True)
        tools = await assembleToolPool(config)

        # ToolSearchTool should have registry set
        tool_search = next(t for t in tools if t.name == "ToolSearch")
        assert len(tool_search._registry) == len(tools)

    @pytest.mark.asyncio
    async def test_tool_search_can_find_deferred(self):
        """ToolSearch can find and load deferred tools from assembled pool."""
        config = Config(deferred_tools=True)
        tools = await assembleToolPool(config)

        tool_search = next(t for t in tools if t.name == "ToolSearch")
        result = await tool_search.execute(query="select:Read,Grep", max_results=5)
        assert not result.error
        assert "Read" in result.output
        assert "Grep" in result.output
        assert result.metadata["schemas_loaded"] > 0

    @pytest.mark.asyncio
    async def test_tool_search_returns_readable_format(self):
        """ToolSearch output is human-readable with descriptions."""
        config = Config(deferred_tools=True)
        tools = await assembleToolPool(config)

        tool_search = next(t for t in tools if t.name == "ToolSearch")
        result = await tool_search.execute(query="select:Glob", max_results=5)
        assert not result.error
        assert "## Glob" in result.output
        # Should have description line
        assert "pattern" in result.output.lower() or "file" in result.output.lower()

    @pytest.mark.asyncio
    async def test_normal_tools_unaffected_in_deferred_mode(self):
        """Small-schema tools remain unwrapped."""
        config = Config(deferred_tools=True)
        tools = await assembleToolPool(config)

        # ToolSearch itself should never be deferred
        ts = next(t for t in tools if t.name == "ToolSearch")
        assert not isinstance(ts, DeferredToolSchema)
