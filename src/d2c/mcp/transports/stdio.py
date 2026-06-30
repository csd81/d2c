"""Stdio transport — spawns a subprocess, communicates via stdin/stdout.

Paper: "The stdio transport spawns a subprocess and communicates via
newline-delimited JSON on stdin/stdout."

This is the most common MCP transport for local tool servers.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from d2c.mcp.transports import MCPTransport


class StdioTransport(MCPTransport):
    """MCP transport over a subprocess's stdin/stdout.

    Each message is a single line of JSON, terminated by newline.
    """

    def __init__(self, command: str, args: list[str] | None = None,
                 env: dict[str, str] | None = None,
                 timeout_ms: int = 30_000) -> None:
        self._command = command
        self._args = args or []
        self._env = env or {}
        self._timeout_ms = timeout_ms / 1000.0  # convert to seconds
        self._process: asyncio.subprocess.Process | None = None
        self._connected = False
        self._buffer = b""

    @property
    def is_connected(self) -> bool:
        return self._connected and self._process is not None and self._process.returncode is None

    async def connect(self) -> None:
        """Spawn the subprocess and establish communication."""
        merged_env = {**os.environ, "PYTHONUNBUFFERED": "1", **self._env}

        try:
            self._process = await asyncio.create_subprocess_exec(
                self._command,
                *self._args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=merged_env,
            )
            self._connected = True
        except FileNotFoundError:
            raise MCPTransportError(
                f"Command not found: {self._command}. "
                f"Make sure the MCP server is installed."
            )
        except Exception as e:
            raise MCPTransportError(f"Failed to spawn {self._command}: {e}")

    async def send(self, message: dict[str, Any]) -> None:
        """Send a JSON-RPC message as a single line of JSON."""
        if not self._process or self._process.stdin is None:
            raise MCPTransportError("Transport not connected")
        line = json.dumps(message, ensure_ascii=False) + "\n"
        self._process.stdin.write(line.encode("utf-8"))
        await self._process.stdin.drain()

    async def receive(self) -> dict[str, Any]:
        """Read a single JSON-RPC message (one line) from stdout."""
        if not self._process or self._process.stdout is None:
            raise MCPTransportError("Transport not connected")

        try:
            while True:
                # Check if we have a complete line in the buffer
                if b"\n" in self._buffer:
                    line, self._buffer = self._buffer.split(b"\n", 1)
                    line_str = line.decode("utf-8").strip()
                    if line_str:
                        return json.loads(line_str)
                    continue

                # Read more data
                try:
                    chunk = await asyncio.wait_for(
                        self._process.stdout.readline(),
                        timeout=self._timeout_ms,
                    )
                except asyncio.TimeoutError:
                    raise MCPTransportError(
                        f"Timeout waiting for response from {self._command}"
                    )

                if not chunk:  # EOF
                    raise MCPTransportError(
                        f"MCP server {self._command} closed stdout (exit code {self._process.returncode})"
                    )

                self._buffer += chunk
        except json.JSONDecodeError as e:
            raise MCPTransportError(f"Invalid JSON from MCP server: {e}")

    async def close(self) -> None:
        """Terminate the subprocess."""
        self._connected = False
        if self._process:
            try:
                if self._process.returncode is None:
                    self._process.terminate()
                    try:
                        await asyncio.wait_for(self._process.wait(), timeout=5.0)
                    except asyncio.TimeoutError:
                        self._process.kill()
                        await self._process.wait()
            except ProcessLookupError:
                pass  # Already exited
            self._process = None


class MCPTransportError(Exception):
    """Raised when an MCP transport encounters an error."""
    pass
