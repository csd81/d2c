# Phase 11: MCP (Model Context Protocol) Integration

**Paper Reference:** Section 6, 3.2 — primary extensibility mechanism for external tools

**Priority:** HIGH

## Rationale

MCP is one of 4 extensibility mechanisms. Without it, the agent cannot connect to external
tool servers, which is the primary way third-party tools integrate (e.g., filesystem servers,
database servers, API gateways). The paper describes MCP as consuming "high context" but
providing the richest integration surface.

## Files to Create/Modify

1. CREATE `src/d2c/mcp/__init__.py`
2. CREATE `src/d2c/mcp/client.py` — MCP client with transport abstraction
3. CREATE `src/d2c/mcp/transports/stdio.py` — stdio transport (subprocess)
4. CREATE `src/d2c/mcp/transports/sse.py` — Server-Sent Events transport
5. CREATE `src/d2c/mcp/transports/http.py` — HTTP transport
6. CREATE `src/d2c/mcp/transports/websocket.py` — WebSocket transport
7. CREATE `src/d2c/mcp/discovery.py` — MCP server discovery from `.d2c/mcp.json` or config
8. MODIFY `src/d2c/tools/pool.py` — merge MCP tools into `assembleToolPool()`

## Key Design

```python
@dataclass
class MCPServerConfig:
    name: str
    command: str | None = None      # stdio
    url: str | None = None          # SSE/HTTP/WS
    transport: str = "stdio"        # stdio | sse | http | websocket
    env: dict = field(default_factory=dict)
    timeout_ms: int = 30_000

class MCPClient:
    """Manages connections to MCP servers, discovers tools, handles lifecycle."""
    async def connect(server: MCPServerConfig) -> None
    async def list_tools() -> list[Tool]
    async def call_tool(name: str, arguments: dict) -> ToolResult
    async def close() -> None
```

**Discovery:** Load servers from `.d2c/mcp.json` and `D2C_MCP_SERVERS` env var.
Each MCP server is connected at session start; its tools are listed and wrapped
as `MCPTool` instances, then merged into the tool pool (MCP tools override
built-ins with the same name).

```python
class MCPTool(Tool):
    name = server_tool.name           # from MCP server
    description = server_tool.description
    input_schema = server_tool.inputSchema
    category = PermissionCategory.READ  # conservative default; configurable
    is_concurrent_safe = True           # MCP tools assumed safe for parallel

    async def execute(self, **kwargs) -> ToolResult:
        return await mcp_client.call_tool(self.name, kwargs)
```

**Config format (`.d2c/mcp.json`):**
```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/allowed"],
      "transport": "stdio"
    },
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "transport": "stdio",
      "env": {"GITHUB_TOKEN": "${GITHUB_TOKEN}"}
    }
  }
}
```

## Edge Cases

- Server connection fails at startup → warn, continue without that server's tools
- Server disconnects mid-session → reconnect once, then mark tools unavailable
- MCP tool name conflicts with built-in → MCP tool wins (user explicitly configured it)
- Large number of tools from MCP (100+) → deferred schema loading (Phase 20)

## Tests (~15)

- stdio transport connects and lists tools
- MCP tool execution calls remote server
- Server disconnect is handled gracefully
- Tool merging: MCP tool overrides built-in of same name
- Empty `.d2c/mcp.json` → no errors
- Server connection failure → warning, session proceeds
- HTTP transport round-trip
- Tool schema is wrapped correctly for Anthropic API format
