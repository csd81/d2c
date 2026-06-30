# Phase 20: Deferred Tool Schemas

**Paper Reference:** Section 3.6 — "When ToolSearch is enabled, some tools include
only their names in the initial context; full schemas are loaded on demand."

**Priority:** LOW

## Rationale

Large tool counts (100+ with MCP) consume significant context. Deferred schemas
conserve context by only including tool names initially, with full schemas loaded
on demand via the ToolSearch tool. This directly addresses the "context as bottleneck"
principle.

## Files to Create/Modify

1. MODIFY `src/d2c/tools/pool.py` — add deferred schema support
2. CREATE `src/d2c/tools/tool_search.py` — ToolSearch tool implementation

## Key Design

```python
class DeferredToolSchema:
    """Wrapper for tools whose full schema is loaded on demand."""
    def __init__(self, tool: Tool):
        self.tool = tool
        self._schema_loaded = False

    def to_api_format(self) -> dict:
        if self._schema_loaded:
            return self.tool.to_api_format()
        return {
            "name": self.tool.name,
            "description": f"Use ToolSearch with query=\"{self.tool.name}\" for full schema.",
        }

    def load_full_schema(self):
        self._schema_loaded = True

class ToolSearchTool(Tool):
    """Enables the model to search for and load tool schemas on demand."""
    name = "ToolSearch"
    description = "Search for tools and load their full schemas."
    category = PermissionCategory.META
    is_concurrent_safe = True

    async def execute(self, query: str = None) -> ToolResult:
        if query:
            matching = [t for t in all_tools if query.lower() in t.name.lower()]
            for tool in matching:
                if isinstance(tool, DeferredToolSchema):
                    tool.load_full_schema()
            return ToolResult(output=format_matches(matching))
        else:
            return ToolResult(output=format_all_tools(all_tools))
```

## Config

`deferred_tools: bool = False` in CompactConfig. When enabled, tools with large
schemas (>500 chars) or MCP tools use deferred loading.

## Tests (~5)

- Deferred tool shows only name in API format
- ToolSearch loads full schema
- ToolSearch fuzzy matches tool names
- ToolSearch with no query returns all tools
- Normal tools unaffected by deferred mode
