"""Deferred tool schemas. Paper Section 3.6.

When many tools are available (100+ with MCP), full schemas consume
significant context. Deferred schemas conserve context by initially
sending only tool names, with full schemas loaded on demand via the
ToolSearch tool.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from d2c.tools import PermissionCategory, Tool, ToolResult

logger = logging.getLogger(__name__)


class DeferredToolSchema:
    """Wrapper for tools whose full schema is deferred from initial context.

    Paper: "When ToolSearch is enabled, some tools include only their names
    in the initial context; full schemas are loaded on demand."

    Until load_full_schema() is called, to_api_format() returns only the
    tool name and a hint to use ToolSearch. After loading, it delegates
    to the wrapped tool's full schema.
    """

    def __init__(self, tool: Tool) -> None:
        self._tool = tool
        self._schema_loaded = False
        self._schema_size = len(str(tool.input_schema))

    @property
    def name(self) -> str:
        return self._tool.name

    @property
    def tool(self) -> Tool:
        return self._tool

    @property
    def is_schema_loaded(self) -> bool:
        return self._schema_loaded

    def to_api_format(self) -> dict[str, Any]:
        """Return abbreviated or full schema depending on load state."""
        if self._schema_loaded:
            return self._tool.to_api_format()
        return {
            "name": self._tool.name,
            "description": (
                f"{self._tool.description} "
                f'[Schema deferred. Use ToolSearch with query="{self._tool.name}" '
                f"to load full schema.]"
            ),
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        }

    def load_full_schema(self) -> None:
        """Load the full schema so subsequent to_api_format() calls return it."""
        if not self._schema_loaded:
            self._schema_loaded = True
            logger.debug("Loaded full schema for tool '%s'", self._tool.name)


class ToolSearchTool(Tool):
    """Enables the model to search for tools and load full schemas on demand.

    Paper: "The ToolSearch tool queries available tools by name or keyword,
    loading full schemas for matches."
    """

    name: ClassVar[str] = "ToolSearch"
    description: ClassVar[str] = (
        "Search for available tools and load their full schemas. "
        "Use this when you see a tool with a deferred schema hint."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query — tool name or keyword. Use 'select:<name>' for direct selection, or keywords to search.",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results to return (default: 5).",
            },
        },
        "required": ["query", "max_results"],
    }
    category: ClassVar[PermissionCategory] = PermissionCategory.META
    is_concurrent_safe: ClassVar[bool] = True

    def __init__(self, tool_registry: list[DeferredToolSchema | Tool] | None = None):
        super().__init__()
        self._registry: list[DeferredToolSchema | Tool] = tool_registry or []

    def set_registry(self, registry: list[DeferredToolSchema | Tool]) -> None:
        """Update the tool registry (called after pool assembly)."""
        self._registry = registry

    async def execute(
        self,
        query: str,
        max_results: int = 5,
    ) -> ToolResult:
        """Search for tools matching the query and load their full schemas.

        Special syntax:
        - "select:ToolA,ToolB" — load schemas for exact tool names
        - Otherwise — fuzzy match against tool names
        """
        # Direct selection mode
        if query.startswith("select:"):
            selector = query[len("select:") :]
            names = [n.strip() for n in selector.split(",") if n.strip()]
            matched: list[Tool] = []
            schemas_loaded = 0
            for name in names:
                for entry in self._registry:
                    tool = entry.tool if isinstance(entry, DeferredToolSchema) else entry
                    if tool.name == name:
                        if isinstance(entry, DeferredToolSchema) and not entry.is_schema_loaded:
                            entry.load_full_schema()
                            schemas_loaded += 1
                        matched.append(tool)
                        break
            if matched:
                output = self._format_results(matched)
                return ToolResult(
                    output=output,
                    metadata={
                        "count": len(matched),
                        "query": query,
                        "matched": [t.name for t in matched],
                        "schemas_loaded": schemas_loaded,
                    },
                )
            else:
                return ToolResult(
                    output=f"No tools found matching: {', '.join(names)}",
                    metadata={"count": 0, "query": query, "schemas_loaded": 0},
                )

        # Keyword search mode
        results: list[Tool] = []
        loaded_count = 0
        query_lower = query.lower()

        for entry in self._registry:
            tool = entry.tool if isinstance(entry, DeferredToolSchema) else entry
            if query_lower in tool.name.lower():
                if isinstance(entry, DeferredToolSchema) and not entry.is_schema_loaded:
                    entry.load_full_schema()
                    loaded_count += 1
                results.append(tool)
                if len(results) >= max_results:
                    break

        if not results:
            return ToolResult(
                output=f"No tools found matching '{query}'. Use ToolSearch with an empty query to list all tools.",
                metadata={"count": 0, "query": query},
            )

        output = self._format_results(results)
        return ToolResult(
            output=output,
            metadata={
                "count": len(results),
                "query": query,
                "schemas_loaded": loaded_count,
                "matched": [t.name for t in results],
            },
        )

    def _format_results(self, tools: list[Tool]) -> str:
        """Format matched tools as a readable summary."""
        lines: list[str] = []
        for tool in tools:
            lines.append(f"## {tool.name}")
            lines.append(f"  {tool.description}")
            schema = tool.to_api_format()
            props = schema.get("input_schema", {}).get("properties", {})
            if props:
                required = schema.get("input_schema", {}).get("required", [])
                for param, info in props.items():
                    req_mark = " (required)" if param in required else ""
                    if isinstance(info, dict):
                        desc = info.get("description", "")
                        lines.append(f"  - {param}{req_mark}: {desc}")
                    else:
                        lines.append(f"  - {param}{req_mark}")
            lines.append("")
        return "\n".join(lines)
