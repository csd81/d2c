"""MCP (Model Context Protocol) Integration.

Paper Section 6, 3.2 — primary extensibility mechanism for external tools.
Provides MCPServerConfig, MCPTool wrapper, and MCPClient for connecting
to MCP-compatible tool servers over stdio, SSE, HTTP, and WebSocket transports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from d2c.tools import PermissionCategory, Tool, ToolResult


@dataclass
class MCPServerConfig:
    """Configuration for a single MCP server connection.

    Paper Section 6: "MCP servers are configured via .d2c/mcp.json or the
    D2C_MCP_SERVERS environment variable."
    """

    name: str
    command: str | None = None  # stdio: executable to spawn
    args: list[str] = field(default_factory=list)  # stdio: arguments
    url: str | None = None  # SSE/HTTP/WebSocket endpoint
    transport: str = "stdio"  # "stdio" | "sse" | "http" | "websocket"
    env: dict[str, str] = field(default_factory=dict)
    timeout_ms: int = 30_000
    headers: dict[str, str] = field(default_factory=dict)  # HTTP/WS headers


class MCPTool(Tool):
    """A Tool implementation that delegates to an MCP server.

    Paper: "Each MCP server is connected at session start; its tools are
    listed and wrapped as MCPTool instances, then merged into the tool pool."

    MCP tools override built-ins with the same name because the user
    explicitly configured the server.
    """

    is_concurrent_safe: bool = True  # MCP tools assumed safe for parallel

    def __init__(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        server_name: str,
        server_config: MCPServerConfig,
    ) -> None:
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.category = PermissionCategory.READ  # conservative default
        self._server_name = server_name
        self._server_config = server_config

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Delegate execution to the MCP client for this server."""
        from d2c.mcp.client import get_client

        client = get_client(self._server_name)
        if client is None:
            return ToolResult(
                output=f"MCP server '{self._server_name}' is not connected.",
                error=True,
            )
        result = await client.call_tool(self.name, kwargs)
        return ToolResult(
            output=result.get("content", [{}])[0].get("text", str(result))
            if isinstance(result, dict)
            else str(result),
            error=result.get("isError", False) if isinstance(result, dict) else False,
        )

    def __repr__(self) -> str:
        return f"<MCPTool {self.name} (server={self._server_name})>"
