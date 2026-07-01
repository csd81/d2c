"""Built-in MCP Server — exposes d2c tools over stdio JSON-RPC.

Paper Section 3.2, 6.1: "Expose d2c tools over MCP for IDE integration."
Implements the Model Context Protocol server-side over stdio transport,
allowing IDEs (Cursor, Zed, VS Code via Cline) to use d2c's tool pool.

Protocol: JSON-RPC 2.0 over stdin/stdout with initialize handshake.
Stdout is reserved for JSON-RPC frames only — tool output is redirected.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from d2c.config import Config
from d2c.tools import Tool
from d2c.tools.pool import Config as PoolConfig
from d2c.tools.pool import assembleToolPool

MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "d2c"
SERVER_VERSION = "0.1.0"


class MCPServer:
    """Stdio JSON-RPC server for Model Context Protocol.

    Reads JSON-RPC requests from stdin, executes d2c tools, and writes
    JSON-RPC responses to stdout. Stderr is used for server logging so
    stdout carries only valid JSON-RPC frames.
    """

    def __init__(self, config: Config, permission_engine: Any = None) -> None:
        self._config = config
        self._tools: list[Tool] = []
        self._tools_map: dict[str, Tool] = {}
        self._initialized = False
        self._request_id = 0
        # Concurrency control: serialize writes, parallelize reads
        self._write_lock = asyncio.Lock()
        # Phase 43: optional permission engine. When set, a non-ALLOW decision
        # (ASK/DENY) fails safe with a permission-required error — MCP has no
        # interactive approval channel, so it never blocks on terminal input.
        self._permission_engine = permission_engine

    # ── Public API ──────────────────────────────────────────────────

    async def start(self) -> None:
        """Main read loop: initialize tools, then process JSON-RPC lines from stdin."""
        # Initialize tool pool
        pool_cfg = PoolConfig(cwd=self._config.cwd)
        self._tools = await assembleToolPool(pool_cfg)
        self._tools_map = {t.name: t for t in self._tools}

        # Log to stderr so stdout stays clean for JSON-RPC
        print(f"[d2c MCP Server] {len(self._tools)} tools loaded", file=sys.stderr)

        # Read JSON-RPC lines from stdin
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        loop = asyncio.get_running_loop()
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        while True:
            try:
                line = await reader.readline()
            except Exception:
                break

            if not line:
                break  # EOF

            try:
                request = json.loads(line.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue  # Skip malformed input

            if not isinstance(request, dict) or "method" not in request:
                continue

            await self._handle_message(request)

    # ── Message dispatch ────────────────────────────────────────────

    async def _handle_message(self, request: dict[str, Any]) -> None:
        """Route JSON-RPC request to the correct handler."""
        method = request.get("method", "")
        msg_id = request.get("id")
        params = request.get("params", {})

        try:
            if method == "initialize":
                response = self._handle_initialize(msg_id, params)
            elif method == "tools/list":
                response = self._handle_list_tools(msg_id)
            elif method == "tools/call":
                response = await self._handle_call_tool(msg_id, params)
            elif method == "notifications/initialized":
                return  # No response for notifications
            else:
                response = self._error_response(
                    msg_id,
                    -32601,
                    f"Method not found: {method}",
                )
        except Exception as exc:
            response = self._error_response(
                msg_id,
                -32603,
                f"Internal error: {exc}",
            )

        if response is not None:
            self._write_response(response)

    # ── Handlers ────────────────────────────────────────────────────

    def _handle_initialize(
        self,
        msg_id: Any,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Handle the initialize handshake."""
        self._initialized = True
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": params.get(
                    "protocolVersion",
                    MCP_PROTOCOL_VERSION,
                ),
                "capabilities": {
                    "tools": {},
                },
                "serverInfo": {
                    "name": SERVER_NAME,
                    "version": SERVER_VERSION,
                },
            },
        }

    def _handle_list_tools(self, msg_id: Any) -> dict[str, Any]:
        """Return the full tool list in MCP format."""
        mcp_tools = []
        for tool in self._tools:
            api_format = tool.to_api_format()
            mcp_tools.append(
                {
                    "name": api_format["name"],
                    "description": api_format.get("description", ""),
                    "inputSchema": api_format.get("input_schema", {}),
                }
            )
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"tools": mcp_tools},
        }

    async def _handle_call_tool(
        self,
        msg_id: Any,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute a tool by name and return the result as MCP content."""
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        tool = self._tools_map.get(tool_name)
        if tool is None:
            return self._error_response(
                msg_id,
                -32602,
                f"Unknown tool: {tool_name}",
            )

        # Guard: enforce safety in untrusted workspaces (Phase 32)
        from d2c.trust import get_trust_gate

        try:
            trusted = get_trust_gate().is_project_trusted
        except RuntimeError:
            trusted = True  # Trust gate not set yet — assume safe

        # For untrusted workspaces, deny shell/write tools
        if not trusted:
            from d2c.tools import PermissionCategory

            category = getattr(tool, "category", None)
            if category in (PermissionCategory.SHELL, PermissionCategory.WRITE):
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    f"Tool '{tool_name}' is not available in "
                                    f"untrusted workspace. Use --trust to enable."
                                ),
                            }
                        ],
                        "isError": True,
                    },
                }

        # Phase 43: if a permission engine is wired, ASK/DENY fail safe (MCP has
        # no interactive approval channel) — never execute automatically.
        if self._permission_engine is not None:
            from d2c.permissions import (
                PermissionDecision,
                PermissionRequest,
                resolve_permission_decision,
            )

            perm_request = PermissionRequest(
                tool_name=tool_name,
                tool_input=arguments,
                tool_category=tool.category,
            )
            try:
                perm_result = await self._permission_engine.evaluate_async(perm_request)
            except Exception as e:
                perm_result = None
                decision_err = type(e).__name__
            else:
                decision_err = None
            resolved = await resolve_permission_decision(perm_request, perm_result, None)
            if decision_err is not None or (
                resolved is not None and resolved.decision != PermissionDecision.ALLOW
            ):
                reason = (
                    decision_err and f"permission check failed ({decision_err})" or resolved.reason
                )
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": f"Permission required for '{tool_name}': {reason}",
                            }
                        ],
                        "isError": True,
                    },
                }

        # Serialize writes to avoid concurrent modifications
        if not getattr(tool, "is_concurrent_safe", False):
            async with self._write_lock:
                result = await tool.execute(**arguments)
        else:
            result = await tool.execute(**arguments)

        # Format result as MCP content blocks
        content_blocks = [
            {
                "type": "text",
                "text": result.output,
            }
        ]
        if result.attachments:
            for att in result.attachments:
                content_blocks.append(att)

        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "content": content_blocks,
                "isError": result.error,
            },
        }

    # ── Helpers ─────────────────────────────────────────────────────

    def _write_response(self, response: dict[str, Any]) -> None:
        """Write a JSON-RPC response to stdout, flushing immediately."""
        line = json.dumps(response, ensure_ascii=False)
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

    def _error_response(
        self,
        msg_id: Any,
        code: int,
        message: str,
    ) -> dict[str, Any]:
        """Build a JSON-RPC error response."""
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {
                "code": code,
                "message": message,
            },
        }


async def run_mcp_server(args: Any) -> None:
    """Entry point for MCP server mode (python -m d2c mcp)."""
    config = Config.load(args.cwd if hasattr(args, "cwd") else Path.cwd())

    # Apply trust resolution (Phase 32 integration)
    from d2c.trust import get_trust_gate

    try:
        get_trust_gate().is_project_trusted
    except RuntimeError:
        # Trust gate not initialized — set up minimal gate
        from d2c.trust import TrustStore, WorkSpaceTrustGate, set_trust_gate

        cwd = (args.cwd if hasattr(args, "cwd") else Path.cwd()).resolve()
        store = TrustStore()
        trusted = store.is_trusted(cwd)
        gate = WorkSpaceTrustGate(cwd, store)
        gate.decide(trusted)
        set_trust_gate(gate)

    server = MCPServer(config)
    await server.start()
