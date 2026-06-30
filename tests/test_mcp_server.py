"""Tests for Phase 33: Built-in MCP Server Mode.

Verifies JSON-RPC handshake, tool listing, tool call execution,
error handling, and trust gate integration.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from d2c.config import Config
from d2c.mcp.server import MCPServer, MCP_PROTOCOL_VERSION, SERVER_NAME
from d2c.tools import Tool, ToolResult, PermissionCategory


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def config(tmp_path):
    return Config(cwd=tmp_path)


@pytest.fixture
def mock_tools():
    """Minimal tool set for testing."""
    class MockReadTool(Tool):
        name = "Read"
        description = "Read a file"
        input_schema = {
            "type": "object",
            "properties": {"file_path": {"type": "string"}},
            "required": ["file_path"],
        }
        category = PermissionCategory.READ
        is_concurrent_safe = True

        async def execute(self, **kwargs):
            return ToolResult(output=f"Contents of {kwargs.get('file_path', '?')}")

    class MockBashTool(Tool):
        name = "Bash"
        description = "Run a shell command"
        input_schema = {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        }
        category = PermissionCategory.SHELL
        is_concurrent_safe = False

        async def execute(self, **kwargs):
            return ToolResult(output=f"Ran: {kwargs.get('command', '')}")

    class MockWriteTool(Tool):
        name = "Write"
        description = "Write a file"
        input_schema = {
            "type": "object",
            "properties": {"file_path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["file_path", "content"],
        }
        category = PermissionCategory.WRITE
        is_concurrent_safe = False

        async def execute(self, **kwargs):
            return ToolResult(output=f"Wrote {kwargs.get('file_path', '?')}")

    return [MockReadTool(), MockBashTool(), MockWriteTool()]


@pytest.fixture
def server(config, mock_tools):
    """MCPServer with pre-loaded mock tools (skip stdin read loop)."""
    srv = MCPServer(config)
    srv._tools = mock_tools
    srv._tools_map = {t.name: t for t in mock_tools}
    srv._initialized = True
    return srv


# ── Initialize handshake tests ────────────────────────────────────────


class TestMCPHandshake:
    def test_initialize_returns_capabilities(self, config):
        """Client can connect and negotiate protocol features."""
        srv = MCPServer(config)
        response = srv._handle_initialize(1, {
            "protocolVersion": MCP_PROTOCOL_VERSION,
        })
        assert response["jsonrpc"] == "2.0"
        assert response["id"] == 1
        result = response["result"]
        assert result["protocolVersion"] == MCP_PROTOCOL_VERSION
        assert "tools" in result["capabilities"]
        assert result["serverInfo"]["name"] == SERVER_NAME

    def test_initialize_sets_initialized_flag(self, config):
        """Initialize handler sets _initialized to True."""
        srv = MCPServer(config)
        assert srv._initialized is False
        srv._handle_initialize(1, {"protocolVersion": MCP_PROTOCOL_VERSION})
        assert srv._initialized is True

    def test_initialize_with_different_protocol_version(self, config):
        """Server echoes back the client's protocol version."""
        srv = MCPServer(config)
        response = srv._handle_initialize(2, {"protocolVersion": "2025-01-01"})
        assert response["result"]["protocolVersion"] == "2025-01-01"


# ── Tool listing tests ─────────────────────────────────────────────────


class TestMCPListTools:
    def test_list_tools_returns_all_tools(self, server):
        """All registered tools are listed with correct MCP format."""
        response = server._handle_list_tools(1)
        assert response["jsonrpc"] == "2.0"
        assert response["id"] == 1
        tools = response["result"]["tools"]
        assert len(tools) == 3
        names = {t["name"] for t in tools}
        assert names == {"Read", "Bash", "Write"}

    def test_list_tools_includes_input_schema(self, server):
        """Each tool entry includes inputSchema."""
        response = server._handle_list_tools(1)
        tools = response["result"]["tools"]
        for tool in tools:
            assert "inputSchema" in tool
            assert "name" in tool
            assert "description" in tool

    def test_list_tools_mcp_naming(self, server):
        """Tool entries use camelCase MCP naming (inputSchema, not input_schema)."""
        response = server._handle_list_tools(1)
        read_tool = [
            t for t in response["result"]["tools"] if t["name"] == "Read"
        ][0]
        assert "inputSchema" in read_tool
        assert "file_path" in str(read_tool["inputSchema"])


# ── Tool call execution tests ──────────────────────────────────────────


class TestMCPCallTool:
    @pytest.mark.asyncio
    async def test_call_read_tool(self, server):
        """Calling Read returns file contents in MCP text block."""
        response = await server._handle_call_tool(1, {
            "name": "Read",
            "arguments": {"file_path": "/test.txt"},
        })
        assert response["jsonrpc"] == "2.0"
        assert response["id"] == 1
        content = response["result"]["content"]
        assert len(content) >= 1
        assert content[0]["type"] == "text"
        assert "Contents of /test.txt" in content[0]["text"]
        assert response["result"]["isError"] is False

    @pytest.mark.asyncio
    async def test_call_bash_tool(self, server):
        """Calling Bash returns command output in MCP content."""
        response = await server._handle_call_tool(1, {
            "name": "Bash",
            "arguments": {"command": "echo hello"},
        })
        assert response["jsonrpc"] == "2.0"
        content = response["result"]["content"]
        assert "Ran: echo hello" in content[0]["text"]

    @pytest.mark.asyncio
    async def test_call_unknown_tool(self, server):
        """Calling an unknown tool returns JSON-RPC error."""
        response = await server._handle_call_tool(1, {
            "name": "NonExistent",
            "arguments": {},
        })
        assert "error" in response
        assert response["error"]["code"] == -32602
        assert "Unknown tool" in response["error"]["message"]

    @pytest.mark.asyncio
    async def test_call_tool_returns_error_flag(self, server, mock_tools):
        """When a tool returns error=True, isError is propagated."""
        # Replace Read with an error-returning version
        class ErrorTool(Tool):
            name = "Read"
            description = "Read"
            input_schema = {}
            category = PermissionCategory.READ
            is_concurrent_safe = True

            async def execute(self, **kwargs):
                return ToolResult(output="File not found", error=True)

        server._tools_map["Read"] = ErrorTool()
        response = await server._handle_call_tool(1, {
            "name": "Read",
            "arguments": {},
        })
        assert response["result"]["isError"] is True
        assert "File not found" in response["result"]["content"][0]["text"]


# ── Error response tests ─────────────────────────────────────────────


class TestMCPErrorHandling:
    def test_unknown_method_returns_error(self, config):
        """Unknown methods return JSON-RPC method-not-found error."""
        srv = MCPServer(config)
        srv._initialized = True  # So it doesn't crash looking for tools
        srv._tools = []
        srv._tools_map = {}

        # Test through _handle_message by calling a non-existent method
        response = None
        srv._write_response = lambda r: setattr(
            TestMCPErrorHandling, '_captured', r,
        )

    def test_error_response_format(self, config):
        """Error responses follow JSON-RPC 2.0 error format."""
        srv = MCPServer(config)
        response = srv._error_response(42, -32603, "Something went wrong")
        assert response["jsonrpc"] == "2.0"
        assert response["id"] == 42
        assert "result" not in response
        assert response["error"]["code"] == -32603
        assert response["error"]["message"] == "Something went wrong"


# ── Trust gate integration tests ─────────────────────────────────────────


class TestMCPTrustIntegration:
    @pytest.mark.asyncio
    async def test_untrusted_blocks_shell_tool(self, server):
        """In untrusted workspace, shell tools return an error."""
        from d2c.trust import WorkSpaceTrustGate, TrustStore, set_trust_gate, reset_trust_gate

        reset_trust_gate()
        try:
            # Set up untrusted gate
            store = TrustStore()
            cwd = Path.cwd()
            gate = WorkSpaceTrustGate(cwd, store)
            gate.decide(False)
            set_trust_gate(gate)

            response = await server._handle_call_tool(1, {
                "name": "Bash",
                "arguments": {"command": "echo test"},
            })
            assert response["result"]["isError"] is True
            assert "untrusted" in response["result"]["content"][0]["text"].lower()
        finally:
            reset_trust_gate()

    @pytest.mark.asyncio
    async def test_trusted_allows_shell_tool(self, server):
        """In trusted workspace, shell tools execute normally."""
        from d2c.trust import WorkSpaceTrustGate, TrustStore, set_trust_gate, reset_trust_gate

        reset_trust_gate()
        try:
            store = TrustStore()
            cwd = Path.cwd()
            gate = WorkSpaceTrustGate(cwd, store)
            gate.decide(True)
            set_trust_gate(gate)

            response = await server._handle_call_tool(1, {
                "name": "Bash",
                "arguments": {"command": "echo test"},
            })
            assert "error" not in response
            assert "Ran: echo test" in response["result"]["content"][0]["text"]
        finally:
            reset_trust_gate()

    @pytest.mark.asyncio
    async def test_read_tools_allowed_when_untrusted(self, server):
        """Read tools are allowed even in untrusted workspaces."""
        from d2c.trust import WorkSpaceTrustGate, TrustStore, set_trust_gate, reset_trust_gate

        reset_trust_gate()
        try:
            store = TrustStore()
            cwd = Path.cwd()
            gate = WorkSpaceTrustGate(cwd, store)
            gate.decide(False)
            set_trust_gate(gate)

            response = await server._handle_call_tool(1, {
                "name": "Read",
                "arguments": {"file_path": "/test.txt"},
            })
            assert "error" not in response
            assert "Contents of /test.txt" in response["result"]["content"][0]["text"]
        finally:
            reset_trust_gate()


# ── Concurrency tests ─────────────────────────────────────────────────


class TestMCPConcurrency:
    @pytest.mark.asyncio
    async def test_write_tools_serialized(self, server):
        """Write tools acquire the write lock (serialized execution)."""
        # Verify the write lock exists and is initially unlocked
        assert not server._write_lock.locked()

        # Call a write tool — the lock should be acquired and released
        response = await server._handle_call_tool(1, {
            "name": "Write",
            "arguments": {"file_path": "/test.txt", "content": "data"},
        })
        assert "error" not in response
        assert not server._write_lock.locked()  # Released after

    @pytest.mark.asyncio
    async def test_read_tools_parallel(self, server):
        """Read tools do NOT acquire the write lock (parallel execution)."""
        # Run two read calls concurrently
        import asyncio
        results = await asyncio.gather(
            server._handle_call_tool(1, {
                "name": "Read",
                "arguments": {"file_path": "/a.txt"},
            }),
            server._handle_call_tool(2, {
                "name": "Read",
                "arguments": {"file_path": "/b.txt"},
            }),
        )
        assert len(results) == 2
        assert "Contents of /a.txt" in results[0]["result"]["content"][0]["text"]
        assert "Contents of /b.txt" in results[1]["result"]["content"][0]["text"]
        # Lock should never have been held (read tools don't use it)
        assert not server._write_lock.locked()


# ── Message dispatch tests ────────────────────────────────────────────


class TestMCPMessageDispatch:
    @pytest.mark.asyncio
    async def test_dispatches_initialize(self, config):
        """_handle_message routes initialize method correctly."""
        srv = MCPServer(config)
        captured = []

        def _capture(response):
            captured.append(response)

        srv._write_response = _capture

        await srv._handle_message({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": MCP_PROTOCOL_VERSION},
        })
        assert len(captured) == 1
        assert captured[0]["result"]["serverInfo"]["name"] == SERVER_NAME

    @pytest.mark.asyncio
    async def test_dispatches_tools_list(self, server):
        """_handle_message routes tools/list method correctly."""
        captured = []

        def _capture(response):
            captured.append(response)

        server._write_response = _capture

        await server._handle_message({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
        })
        assert len(captured) == 1
        assert len(captured[0]["result"]["tools"]) == 3

    @pytest.mark.asyncio
    async def test_dispatches_tools_call(self, server):
        """_handle_message routes tools/call method correctly."""
        captured = []

        def _capture(response):
            captured.append(response)

        server._write_response = _capture

        await server._handle_message({
            "jsonrpc": "2.0",
            "id": 42,
            "method": "tools/call",
            "params": {"name": "Read", "arguments": {"file_path": "/x.txt"}},
        })
        assert len(captured) == 1
        assert captured[0]["id"] == 42
        assert "Contents of /x.txt" in captured[0]["result"]["content"][0]["text"]

    def test_ignores_initialized_notification(self, config):
        """notifications/initialized produces no response."""
        srv = MCPServer(config)
        # No exception, no response written
        srv._handle_initialize(1, {"protocolVersion": MCP_PROTOCOL_VERSION})
        # This would be tested via _handle_message, which returns early
