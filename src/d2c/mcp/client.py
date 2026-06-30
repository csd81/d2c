"""MCP Client — manages lifecycle, tool discovery, and tool execution.

Paper Section 6: "MCPClient manages connections to MCP servers, discovers
tools, and handles lifecycle (connect, list_tools, call_tool, close)."

Protocol: JSON-RPC 2.0 with initialize/initialized handshake.
"""

from __future__ import annotations

import logging
from typing import Any

from d2c.mcp import MCPServerConfig
from d2c.mcp.transports import MCPTransport
from d2c.mcp.transports.stdio import MCPTransportError, StdioTransport
from d2c.mcp.transports.sse import SSETransport
from d2c.mcp.transports.http import HTTPTransport
from d2c.mcp.transports.websocket import WebSocketTransport

logger = logging.getLogger(__name__)

# Global registry of connected MCP clients, keyed by server name.
# MCPTool.execute() uses this to find the client for its server.
_clients: dict[str, "MCPClient"] = {}


def get_client(server_name: str) -> "MCPClient | None":
    """Look up a connected MCP client by server name."""
    return _clients.get(server_name)


def _register_client(name: str, client: "MCPClient") -> None:
    _clients[name] = client


def _unregister_client(name: str) -> None:
    _clients.pop(name, None)


MCP_PROTOCOL_VERSION = "2024-11-05"


class MCPClient:
    """Manages a connection to a single MCP server.

    Lifecycle: connect() → list_tools() → call_tool() → close()

    JSON-RPC message IDs are auto-incremented.
    """

    def __init__(self, config: MCPServerConfig) -> None:
        self._config = config
        self._transport: MCPTransport | None = None
        self._request_id = 0
        self._server_capabilities: dict[str, Any] = {}
        self._server_info: dict[str, Any] = {}
        self._tools: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return self._config.name

    @property
    def is_connected(self) -> bool:
        return self._transport is not None and self._transport.is_connected

    @property
    def tools(self) -> list[dict[str, Any]]:
        return list(self._tools)

    def _create_transport(self) -> MCPTransport:
        """Factory: create the appropriate transport based on config."""
        transport_type = self._config.transport.lower()

        if transport_type == "stdio":
            return StdioTransport(
                command=self._config.command or "",
                args=self._config.args,
                env=self._config.env,
                timeout_ms=self._config.timeout_ms,
            )
        elif transport_type == "sse":
            return SSETransport(
                url=self._config.url or "",
                headers=self._config.headers,
                timeout_ms=self._config.timeout_ms,
            )
        elif transport_type == "http":
            return HTTPTransport(
                url=self._config.url or "",
                headers=self._config.headers,
                timeout_ms=self._config.timeout_ms,
            )
        elif transport_type == "websocket":
            return WebSocketTransport(
                url=self._config.url or "",
                headers=self._config.headers,
                timeout_ms=self._config.timeout_ms,
            )
        else:
            raise MCPTransportError(
                f"Unknown transport type: {transport_type}. "
                f"Supported: stdio, sse, http, websocket"
            )

    async def connect(self) -> None:
        """Connect to the MCP server and perform the initialize handshake."""
        self._transport = self._create_transport()
        await self._transport.connect()

        # JSON-RPC initialize handshake
        init_result = await self._send_request("initialize", {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {
                "tools": {},
            },
            "clientInfo": {
                "name": "d2c",
                "version": "0.1.0",
            },
        })

        self._server_capabilities = init_result.get("capabilities", {})
        self._server_info = init_result.get("serverInfo", {})

        # Send initialized notification (no response expected)
        await self._send_notification("notifications/initialized", {})

        logger.info(
            "MCP server '%s' connected: %s %s (protocol %s)",
            self._config.name,
            self._server_info.get("name", "unknown"),
            self._server_info.get("version", ""),
            init_result.get("protocolVersion", "unknown"),
        )

        _register_client(self._config.name, self)

    async def list_tools(self) -> list[dict[str, Any]]:
        """Discover tools from the MCP server. Caches results locally."""
        if not self._transport:
            raise MCPTransportError("Not connected")

        result = await self._send_request("tools/list", {})
        self._tools = result.get("tools", [])
        return self._tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute a tool on the MCP server."""
        if not self._transport:
            raise MCPTransportError("Not connected")

        return await self._send_request("tools/call", {
            "name": name,
            "arguments": arguments,
        })

    async def close(self) -> None:
        """Close the connection and unregister."""
        _unregister_client(self._config.name)
        if self._transport:
            await self._transport.close()
            self._transport = None

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def _send_request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send a JSON-RPC request and return the result."""
        msg_id = self._next_id()
        request = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": method,
            "params": params,
        }

        if self._transport is None:
            raise MCPTransportError("Transport not connected")

        # For HTTP transport, send and receive are coupled
        if isinstance(self._transport, HTTPTransport):
            await self._transport.send(request)
            response = await self._transport.receive()
        else:
            await self._transport.send(request)
            response = await self._transport.receive()

        return self._parse_response(response, msg_id)

    async def _send_notification(self, method: str, params: dict[str, Any]) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        if self._transport is None:
            raise MCPTransportError("Transport not connected")
        await self._transport.send(notification)

    def _parse_response(self, response: dict[str, Any], expected_id: int) -> dict[str, Any]:
        """Validate JSON-RPC response and extract result or raise on error."""
        if "error" in response:
            err = response["error"]
            raise MCPTransportError(
                f"MCP error {err.get('code', '')}: {err.get('message', str(err))}"
            )

        if response.get("id") != expected_id:
            raise MCPTransportError(
                f"Response id mismatch: expected {expected_id}, got {response.get('id')}"
            )

        return response.get("result", {})
