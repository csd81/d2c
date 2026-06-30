"""MCP transport layer — abstract base and concrete implementations.

Paper Section 6: "MCP supports four transport mechanisms: stdio (subprocess),
Server-Sent Events (SSE), HTTP POST, and WebSocket."

Each transport implements a common interface: connect, send, receive, close.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class MCPTransport(ABC):
    """Abstract base for MCP transport backends."""

    @abstractmethod
    async def connect(self) -> None:
        """Establish the transport connection."""
        ...

    @abstractmethod
    async def send(self, message: dict[str, Any]) -> None:
        """Send a JSON-RPC message to the server."""
        ...

    @abstractmethod
    async def receive(self) -> dict[str, Any]:
        """Receive a JSON-RPC message from the server."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Close the transport connection."""
        ...

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Whether the transport is currently connected."""
        ...
