"""SSE (Server-Sent Events) transport for MCP.

Paper: "SSE transport: server→client messages arrive via an event stream;
client→server messages are POSTed to a companion HTTP endpoint."

The server sends events on the SSE stream. The client POSTs JSON-RPC
messages to the same base URL (or a /messages endpoint).
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from d2c.mcp.transports import MCPTransport
from d2c.mcp.transports.stdio import MCPTransportError

if TYPE_CHECKING:
    pass


def _get_httpx():
    """Lazy import httpx — only fails when SSE transport is actually used."""
    import httpx

    return httpx


class SSETransport(MCPTransport):
    """MCP transport over Server-Sent Events + HTTP POST."""

    def __init__(
        self, url: str, headers: dict[str, str] | None = None, timeout_ms: int = 30_000
    ) -> None:
        self._url = url.rstrip("/")
        self._headers = headers or {}
        self._timeout_ms = timeout_ms
        self._timeout = timeout_ms / 1000.0
        self._client = None
        self._connected = False
        self._event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._receive_task: asyncio.Task | None = None
        self._message_url: str = self._url  # POST endpoint (same URL by default)

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        """Open SSE connection and start consuming events."""
        httpx = _get_httpx()
        self._client = httpx.AsyncClient(
            headers={
                "Accept": "text/event-stream",
                "Content-Type": "application/json",
                **self._headers,
            },
            timeout=httpx.Timeout(self._timeout, connect=10.0),
        )

        try:
            # Start the SSE event stream reader
            self._receive_task = asyncio.create_task(self._read_sse_stream())
            self._connected = True
        except Exception as e:
            await self._client.aclose()
            self._client = None
            raise MCPTransportError(f"Failed to connect SSE to {self._url}: {e}")

    async def _read_sse_stream(self) -> None:
        """Consume the SSE event stream, parsing events into the queue."""
        if not self._client:
            return

        try:
            async with self._client.stream("GET", self._url) as response:
                if response.status_code != 200:
                    raise MCPTransportError(f"SSE connection failed: HTTP {response.status_code}")

                event_data = ""
                async for line in response.aiter_lines():
                    if line == "":
                        # Empty line = end of event
                        if event_data:
                            try:
                                msg = json.loads(event_data)
                                self._event_queue.put_nowait(msg)
                            except json.JSONDecodeError:
                                pass  # Non-JSON event, skip
                            event_data = ""
                        continue

                    if line.startswith("event:"):
                        pass  # SSE event type is currently unused
                    elif line.startswith("data:"):
                        event_data += line[5:].strip()
                    elif line.startswith(":"):
                        continue  # Comment
                    else:
                        event_data += line.strip()

                # Stream ended
                self._connected = False
        except Exception:
            self._connected = False
            if self._client:
                await self._client.aclose()
            raise

    async def send(self, message: dict[str, Any]) -> None:
        """POST a JSON-RPC message to the server."""
        if not self._client:
            raise MCPTransportError("SSE transport not connected")

        payload = json.dumps(message, ensure_ascii=False)
        resp = await self._client.post(
            self._message_url,
            content=payload,
        )
        if resp.status_code >= 400:
            raise MCPTransportError(f"SSE POST failed: HTTP {resp.status_code}: {resp.text[:500]}")

    async def receive(self) -> dict[str, Any]:
        """Wait for the next event from the SSE stream."""
        try:
            return await asyncio.wait_for(
                self._event_queue.get(),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            raise MCPTransportError(f"Timeout waiting for SSE event from {self._url}")

    async def close(self) -> None:
        """Close the SSE connection."""
        self._connected = False
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
        if self._client:
            await self._client.aclose()
            self._client = None
