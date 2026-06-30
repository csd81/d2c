"""HTTP transport for MCP — each JSON-RPC exchange is a single POST request.

Paper: "HTTP transport: each message is a POST with JSON body and JSON response.
Simplest transport, no persistent connection needed."
"""

from __future__ import annotations

import json
from typing import Any

from d2c.mcp.transports import MCPTransport
from d2c.mcp.transports.stdio import MCPTransportError


def _get_httpx():
    """Lazy import httpx — only fails when HTTP transport is actually used."""
    import httpx
    return httpx


class HTTPTransport(MCPTransport):
    """MCP transport over HTTP POST request/response.

    Each send+receive is a single HTTP POST. No persistent connection.
    """

    def __init__(self, url: str, headers: dict[str, str] | None = None,
                 timeout_ms: int = 30_000) -> None:
        self._url = url.rstrip("/")
        self._headers = headers or {}
        self._timeout = timeout_ms / 1000.0
        self._client = None
        self._connected = False
        self._pending_response: dict[str, Any] | None = None

    @property
    def is_connected(self) -> bool:
        # HTTP is connectionless — we're "connected" once the client exists
        return self._client is not None

    async def connect(self) -> None:
        """Initialize the HTTP client."""
        httpx = _get_httpx()
        self._client = httpx.AsyncClient(
            headers={
                "Content-Type": "application/json",
                **self._headers,
            },
            timeout=httpx.Timeout(self._timeout, connect=10.0),
        )
        self._connected = True

    async def send(self, message: dict[str, Any]) -> None:
        """POST the message and store the response for receive()."""
        if not self._client:
            raise MCPTransportError("HTTP transport not connected")

        payload = json.dumps(message, ensure_ascii=False)
        resp = await self._client.post(self._url, content=payload)

        if resp.status_code >= 400:
            raise MCPTransportError(
                f"HTTP POST failed: {resp.status_code}: {resp.text[:500]}"
            )

        try:
            self._pending_response = resp.json()
        except json.JSONDecodeError as e:
            raise MCPTransportError(f"Invalid JSON response: {e}")

    async def receive(self) -> dict[str, Any]:
        """Return the response from the last send()."""
        if self._pending_response is None:
            raise MCPTransportError("No pending response — call send() first")
        result = self._pending_response
        self._pending_response = None
        return result

    async def close(self) -> None:
        """Close the HTTP client."""
        self._connected = False
        if self._client:
            await self._client.aclose()
            self._client = None
