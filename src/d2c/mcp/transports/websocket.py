"""WebSocket transport for MCP — persistent bidirectional connection.

Paper: "WebSocket transport: full-duplex persistent connection with
JSON text frames for each message."

Uses the websockets library for async WebSocket support.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from d2c.mcp.transports import MCPTransport
from d2c.mcp.transports.stdio import MCPTransportError


def _get_websockets():
    """Lazy import websockets — only fails when WebSocket transport is actually used."""
    import websockets
    import websockets.exceptions

    return websockets


class WebSocketTransport(MCPTransport):
    """MCP transport over a WebSocket connection.

    Each message is a JSON text frame. Bidirectional, persistent.
    """

    def __init__(
        self, url: str, headers: dict[str, str] | None = None, timeout_ms: int = 30_000
    ) -> None:
        self._url = url
        self._headers = headers or {}
        self._timeout = timeout_ms / 1000.0
        self._ws = None
        self._connected = False
        self._receive_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._receive_task: asyncio.Task | None = None

    @property
    def is_connected(self) -> bool:
        return self._connected and self._ws is not None

    async def connect(self) -> None:
        """Establish WebSocket connection."""
        websockets = _get_websockets()
        try:
            extra_headers = [(k, v) for k, v in self._headers.items()]
            self._ws = await websockets.connect(
                self._url,
                extra_headers=extra_headers if extra_headers else None,
                close_timeout=5,
            )
            self._connected = True
            self._receive_task = asyncio.create_task(self._read_loop())
        except Exception as e:
            raise MCPTransportError(f"WebSocket connection failed to {self._url}: {e}")

    async def _read_loop(self) -> None:
        """Continuously read messages from the WebSocket into the queue."""
        websockets = _get_websockets()
        try:
            while self._ws:
                try:
                    raw = await asyncio.wait_for(
                        self._ws.recv(),
                        timeout=self._timeout,
                    )
                except asyncio.TimeoutError:
                    continue  # No message within timeout, keep waiting

                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                try:
                    msg = json.loads(raw)
                    self._receive_queue.put_nowait(msg)
                except json.JSONDecodeError:
                    pass  # Non-JSON frame, skip
        except websockets.exceptions.ConnectionClosed:
            self._connected = False
        except Exception:
            self._connected = False

    async def send(self, message: dict[str, Any]) -> None:
        """Send a JSON-RPC message as a text frame."""
        if not self._ws:
            raise MCPTransportError("WebSocket not connected")
        payload = json.dumps(message, ensure_ascii=False)
        websockets = _get_websockets()
        try:
            await self._ws.send(payload)
        except websockets.exceptions.ConnectionClosed as e:
            raise MCPTransportError(f"WebSocket closed: {e}")

    async def receive(self) -> dict[str, Any]:
        """Receive the next message from the queue."""
        try:
            return await asyncio.wait_for(
                self._receive_queue.get(),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            raise MCPTransportError("Timeout waiting for WebSocket message")

    async def close(self) -> None:
        """Close the WebSocket connection."""
        self._connected = False
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            await self._ws.close()
            self._ws = None
