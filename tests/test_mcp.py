"""Tests for Phase 11: MCP Integration.

Covers: transport layer, MCPServerConfig, client lifecycle, tool wrapping,
discovery from mcp.json, pool integration, and edge cases.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from d2c.mcp import MCPServerConfig, MCPTool
from d2c.mcp.transports.stdio import MCPTransportError, StdioTransport
from d2c.mcp.client import MCPClient, get_client, _clients
from d2c.mcp.discovery import discover_servers, _parse_single_server, _expand_env_vars
from d2c.tools import PermissionCategory, ToolResult


# ── MCPServerConfig ──────────────────────────────────────────────────────

class TestMCPServerConfig:
    def test_defaults(self):
        cfg = MCPServerConfig(name="test")
        assert cfg.name == "test"
        assert cfg.transport == "stdio"
        assert cfg.timeout_ms == 30_000
        assert cfg.command is None
        assert cfg.url is None
        assert cfg.args == []
        assert cfg.env == {}
        assert cfg.headers == {}

    def test_stdio_config(self):
        cfg = MCPServerConfig(
            name="filesystem",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            transport="stdio",
        )
        assert cfg.command == "npx"
        assert len(cfg.args) == 3
        assert cfg.transport == "stdio"

    def test_http_config(self):
        cfg = MCPServerConfig(
            name="remote",
            url="https://example.com/mcp",
            transport="http",
            headers={"Authorization": "Bearer token"},
        )
        assert cfg.url == "https://example.com/mcp"
        assert cfg.headers["Authorization"] == "Bearer token"


# ── MCPTool wrapper ──────────────────────────────────────────────────────

class TestMCPTool:
    def test_tool_attributes(self):
        tool = MCPTool(
            name="read_file",
            description="Read a file from the server",
            input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
            server_name="filesystem",
            server_config=MCPServerConfig(name="filesystem", command="npx"),
        )
        assert tool.name == "read_file"
        assert tool.description == "Read a file from the server"
        assert tool.category == PermissionCategory.READ
        assert tool.is_concurrent_safe is True
        assert "filesystem" in repr(tool)

    def test_to_api_format(self):
        tool = MCPTool(
            name="search",
            description="Search the codebase",
            input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
            server_name="search-server",
            server_config=MCPServerConfig(name="search-server", command="search"),
        )
        api = tool.to_api_format()
        assert api["name"] == "search"
        assert api["description"] == "Search the codebase"
        assert api["input_schema"]["properties"]["query"]["type"] == "string"

    @pytest.mark.asyncio
    async def test_execute_when_disconnected(self):
        tool = MCPTool(
            name="test_tool",
            description="A test tool",
            input_schema={"type": "object", "properties": {}},
            server_name="nonexistent",
            server_config=MCPServerConfig(name="nonexistent", command="nonexistent"),
        )
        result = await tool.execute()
        assert result.error is True
        assert "not connected" in result.output


# ── Transport: StdioTransport ────────────────────────────────────────────

class TestStdioTransport:
    @pytest.mark.asyncio
    async def test_connect_spawns_process(self):
        """stdio transport connects to a subprocess."""
        transport = StdioTransport(
            command=sys_executable(),
            args=["-c", "import sys, json; sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':1,'result':{}})+'\\n')"],
        )
        try:
            await transport.connect()
            assert transport.is_connected
        finally:
            await transport.close()

    @pytest.mark.asyncio
    async def test_send_and_receive(self):
        """Round-trip: send a request, receive a response."""
        script = (
            "import sys, json; "
            "line = sys.stdin.readline(); "
            "req = json.loads(line); "
            "resp = {'jsonrpc':'2.0','id':req['id'],'result':{'echo': req['params'].get('msg','')}}; "
            "sys.stdout.write(json.dumps(resp)+'\\n')"
        )
        transport = StdioTransport(
            command=sys_executable(),
            args=["-c", script],
        )
        try:
            await transport.connect()
            await transport.send({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "echo",
                "params": {"msg": "hello"},
            })
            response = await transport.receive()
            assert response["result"]["echo"] == "hello"
        finally:
            await transport.close()

    @pytest.mark.asyncio
    async def test_command_not_found(self):
        """Graceful error when executable doesn't exist."""
        transport = StdioTransport(command="nonexistent_command_xyz")
        with pytest.raises(MCPTransportError, match="Command not found"):
            await transport.connect()

    @pytest.mark.asyncio
    async def test_error_before_connect(self):
        """Error when using transport before connecting."""
        transport = StdioTransport(command="echo")
        with pytest.raises(MCPTransportError, match="not connected"):
            await transport.send({"jsonrpc": "2.0", "id": 1, "method": "test"})


# ── Environment variable expansion ───────────────────────────────────────

class TestEnvVarExpansion:
    def test_basic_expansion(self):
        os.environ["TEST_MCP_VAR"] = "expanded_value"
        result = _expand_env_vars("prefix_${TEST_MCP_VAR}_suffix")
        assert result == "prefix_expanded_value_suffix"

    def test_dollar_sign_expansion(self):
        os.environ["TEST_MCP_VAR2"] = "val2"
        result = _expand_env_vars("$TEST_MCP_VAR2")
        assert result == "val2"

    def test_unknown_var_unchanged(self):
        result = _expand_env_vars("${NONEXISTENT_VAR_XYZ}")
        assert result == "${NONEXISTENT_VAR_XYZ}"


# ── Server config parsing ────────────────────────────────────────────────

class TestParseServerConfig:
    def test_stdio_server(self):
        cfg = _parse_single_server("test", {
            "command": "node",
            "args": ["server.js"],
            "transport": "stdio",
            "env": {"NODE_ENV": "production"},
        })
        assert cfg.name == "test"
        assert cfg.command == "node"
        assert cfg.args == ["server.js"]
        assert cfg.transport == "stdio"
        assert cfg.env == {"NODE_ENV": "production"}

    def test_http_server(self):
        cfg = _parse_single_server("api", {
            "url": "https://mcp.example.com/api",
            "transport": "http",
            "headers": {"X-API-Key": "secret"},
        })
        assert cfg.name == "api"
        assert cfg.url == "https://mcp.example.com/api"
        assert cfg.transport == "http"
        assert cfg.headers == {"X-API-Key": "secret"}

    def test_defaults(self):
        cfg = _parse_single_server("minimal", {"command": "echo"})
        assert cfg.transport == "stdio"
        assert cfg.args == []
        assert cfg.env == {}


# ── Discovery ────────────────────────────────────────────────────────────

class TestDiscovery:
    def test_empty_when_no_config(self, monkeypatch):
        """No mcp.json and no env var → empty list."""
        monkeypatch.delenv("D2C_MCP_SERVERS", raising=False)
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            (cwd / ".d2c").mkdir(exist_ok=True)
            servers = discover_servers(cwd)
            assert servers == []

    def test_load_from_mcp_json(self):
        """Load servers from a project .d2c/mcp.json."""
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            d2c_dir = cwd / ".d2c"
            d2c_dir.mkdir(exist_ok=True)
            mcp_json = d2c_dir / "mcp.json"
            mcp_json.write_text(json.dumps({
                "mcpServers": {
                    "test-server": {
                        "command": "echo",
                        "args": ["hello"],
                    }
                }
            }))

            servers = discover_servers(cwd)
            assert len(servers) == 1
            assert servers[0].name == "test-server"
            assert servers[0].command == "echo"
            assert servers[0].args == ["hello"]

    def test_empty_mcp_json(self):
        """Empty mcpServers → no configs."""
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            d2c_dir = cwd / ".d2c"
            d2c_dir.mkdir(exist_ok=True)
            (d2c_dir / "mcp.json").write_text(json.dumps({"mcpServers": {}}))

            servers = discover_servers(cwd)
            assert servers == []

    def test_mcp_json_not_found(self):
        """No mcp.json exists → no error, empty list."""
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            (cwd / ".d2c").mkdir(exist_ok=True)
            servers = discover_servers(cwd)
            assert servers == []

    def test_env_var_servers(self, monkeypatch):
        """D2C_MCP_SERVERS env var as JSON object."""
        monkeypatch.setenv("D2C_MCP_SERVERS", json.dumps({
            "mcpServers": {
                "env-server": {
                    "command": "python",
                    "args": ["-m", "mcp_server"],
                }
            }
        }))
        servers = discover_servers()
        assert any(s.name == "env-server" for s in servers)


# ── MCPClient lifecycle ──────────────────────────────────────────────────

class MockTransport:
    """Mock MCP transport for testing MCPClient without real subprocesses."""

    def __init__(self, responses: list[dict[str, Any]] | None = None):
        self.responses = responses or []
        self._response_index = 0
        self.sent_messages: list[dict[str, Any]] = []
        self._connected = False
        self._closed = False

    @property
    def is_connected(self) -> bool:
        return self._connected and not self._closed

    async def connect(self) -> None:
        self._connected = True

    async def send(self, message: dict[str, Any]) -> None:
        self.sent_messages.append(message)

    async def receive(self) -> dict[str, Any]:
        if self._response_index >= len(self.responses):
            return {"jsonrpc": "2.0", "id": self.sent_messages[-1]["id"], "result": {}}
        resp = self.responses[self._response_index]
        self._response_index += 1
        return resp

    async def close(self) -> None:
        self._closed = True
        self._connected = False


class TestMCPClientLifecycle:
    @pytest.mark.asyncio
    async def test_connect_handshake(self):
        """Client performs initialize → initialized handshake."""
        transport = MockTransport(responses=[{
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "test-server", "version": "1.0"},
            },
        }])
        config = MCPServerConfig(name="test", command="mock", transport="stdio")
        client = MCPClient(config)
        client._create_transport = lambda: transport  # inject mock
        try:
            await client.connect()
            assert client.is_connected
            assert client.name == "test"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_close_cleans_up(self):
        """Close disconnects and unregisters."""
        transport = MockTransport(responses=[{
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "serverInfo": {},
            },
        }])
        config = MCPServerConfig(name="cleanup-test", command="mock")
        client = MCPClient(config)
        client._create_transport = lambda: transport
        await client.connect()
        assert get_client("cleanup-test") is client
        await client.close()
        assert not client.is_connected
        assert get_client("cleanup-test") is None

    @pytest.mark.asyncio
    async def test_list_tools(self):
        """Client can list tools from a server."""
        transport = MockTransport(responses=[
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {},
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {
                    "tools": [
                        {"name": "tool_a", "description": "First tool", "inputSchema": {"type": "object", "properties": {}}},
                        {"name": "tool_b", "description": "Second tool", "inputSchema": {"type": "object", "properties": {}}},
                    ]
                },
            },
        ])
        config = MCPServerConfig(name="tools-test", command="mock")
        client = MCPClient(config)
        client._create_transport = lambda: transport
        try:
            await client.connect()
            tools = await client.list_tools()
            assert len(tools) == 2
            assert tools[0]["name"] == "tool_a"
            assert tools[1]["name"] == "tool_b"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_call_tool(self):
        """Client can execute a tool and get results."""
        transport = MockTransport(responses=[
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {},
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {
                    "content": [{"type": "text", "text": "Called my_tool with {'key': 'value'}"}],
                },
            },
        ])
        config = MCPServerConfig(name="call-test", command="mock")
        client = MCPClient(config)
        client._create_transport = lambda: transport
        try:
            await client.connect()
            result = await client.call_tool("my_tool", {"key": "value"})
            content = result["content"][0]["text"]
            assert "my_tool" in content
            assert "key" in content
        finally:
            await client.close()


# ── Tool pool integration ────────────────────────────────────────────────

class TestPoolIntegration:
    @pytest.mark.asyncio
    async def test_mcp_tools_in_pool(self, monkeypatch):
        """assembleMCPTools returns MCPTool instances."""
        # Mock discover_servers to return a known config
        from d2c.mcp import discovery
        monkeypatch.setattr(
            discovery, "discover_servers",
            lambda cwd=None: [MCPServerConfig(
                name="pool-test",
                command="mock",
                transport="stdio",
            )]
        )

        # Mock MCPClient to use mock transport
        transport = MockTransport(responses=[
            {"jsonrpc": "2.0", "id": 1, "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {},
            }},
            {"jsonrpc": "2.0", "id": 2, "result": {
                "tools": [{
                    "name": "mcp_search",
                    "description": "Search via MCP",
                    "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}},
                }]
            }},
        ])

        from d2c.mcp.client import MCPClient as RealMCPClient
        original_create = RealMCPClient._create_transport
        def mock_create(self):
            return transport
        monkeypatch.setattr(RealMCPClient, "_create_transport", mock_create)

        from d2c.tools.pool import assembleMCPTools
        tools = await assembleMCPTools()
        assert len(tools) == 1
        assert tools[0].name == "mcp_search"
        assert isinstance(tools[0], MCPTool)

        # Cleanup
        from d2c.mcp.client import _clients
        for name in list(_clients.keys()):
            await _clients[name].close()

    @pytest.mark.asyncio
    async def test_assemble_tool_pool_includes_mcp(self, monkeypatch):
        """assembleToolPool integrates MCP tools alongside built-ins."""
        from d2c.mcp import discovery
        monkeypatch.setattr(
            discovery, "discover_servers",
            lambda cwd=None: [MCPServerConfig(
                name="override-test",
                command="mock",
                transport="stdio",
            )]
        )

        # MCP tool named "bash" should override built-in bash tool
        transport = MockTransport(responses=[
            {"jsonrpc": "2.0", "id": 1, "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {},
            }},
            {"jsonrpc": "2.0", "id": 2, "result": {
                "tools": [{
                    "name": "bash",
                    "description": "MCP bash override",
                    "inputSchema": {"type": "object", "properties": {}},
                }]
            }},
        ])

        from d2c.mcp.client import MCPClient as RealMCPClient
        def mock_create(self):
            return transport
        monkeypatch.setattr(RealMCPClient, "_create_transport", mock_create)

        from d2c.tools.pool import assembleToolPool, Config
        config = Config(cwd=Path.cwd())
        tools = await assembleToolPool(config)

        # The 'bash' tool should now be an MCPTool (override)
        bash_tools = [t for t in tools if t.name == "bash"]
        assert len(bash_tools) == 1
        assert isinstance(bash_tools[0], MCPTool)

        # Cleanup
        from d2c.mcp.client import _clients
        for name in list(_clients.keys()):
            await _clients[name].close()


# ── Edge cases ───────────────────────────────────────────────────────────

class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_server_connection_failure_graceful(self):
        """Server that fails to start → warning, session proceeds."""
        config = MCPServerConfig(
            name="failing",
            command="nonexistent_binary_xyz",
            transport="stdio",
        )
        client = MCPClient(config)
        with pytest.raises(MCPTransportError):
            await client.connect()

    @pytest.mark.asyncio
    async def test_invalid_json_from_server(self):
        """Server returns non-JSON → transport error."""
        script = "import sys; sys.stdout.write('not json\\n'); sys.stdout.flush()"
        transport = StdioTransport(
            command=sys_executable(),
            args=["-c", script],
        )
        try:
            await transport.connect()
            with pytest.raises(MCPTransportError, match="Invalid JSON"):
                await transport.receive()
        finally:
            await transport.close()

    @pytest.mark.asyncio
    async def test_jsonrpc_error_response(self):
        """Server returns a JSON-RPC error → MCPTransportError."""
        transport = MockTransport(responses=[
            {"jsonrpc": "2.0", "id": 1, "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "serverInfo": {},
            }},
            {"jsonrpc": "2.0", "id": 2, "error": {"code": -32600, "message": "Invalid Request"}},
        ])
        config = MCPServerConfig(name="error-test", command="mock")
        client = MCPClient(config)
        client._create_transport = lambda: transport
        try:
            await client.connect()
            with pytest.raises(MCPTransportError, match="Invalid Request"):
                await client.list_tools()
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_discover_servers_with_broken_json(self):
        """Malformed mcp.json → logged warning, empty result."""
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            d2c_dir = cwd / ".d2c"
            d2c_dir.mkdir(exist_ok=True)
            (d2c_dir / "mcp.json").write_text("this is not json {{{")

            servers = discover_servers(cwd)
            assert servers == []

    def test_unknown_transport_raises(self):
        """Unknown transport type → MCPTransportError."""
        config = MCPServerConfig(
            name="bad",
            command="echo",
            transport="grpc",  # unsupported
        )
        client = MCPClient(config)
        # _create_transport is called during connect()
        with pytest.raises(MCPTransportError, match="Unknown transport"):
            client._create_transport()


# ── Helpers ──────────────────────────────────────────────────────────────

def sys_executable() -> str:
    import sys
    return sys.executable


def teardown_module():
    """Clean up any leftover MCP clients."""
    for name in list(_clients.keys()):
        try:
            asyncio.get_event_loop().run_until_complete(_clients[name].close())
        except Exception:
            pass
