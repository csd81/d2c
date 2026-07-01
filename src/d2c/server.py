"""Local HTTP server exposing d2c session/health endpoints (Phase 59).

Groundwork for a local daemon — not a production HTTP server. Hand-rolled
minimal HTTP/1.1 request/response handling over asyncio streams (no new
dependency): JSON in, JSON out, one request per connection
(``Connection: close``), no auth, no chunked transfer-encoding. Binds to
``127.0.0.1`` by default; only bind elsewhere if you understand the
exposure (there is no authentication layer).

Endpoints:
    GET  /health                    -> {"status": "ok", "version": "..."}
    POST /sessions                  -> {"session_id": "..."}
    POST /sessions/{id}/messages    -> {"session_id", "text", "stop_reason"}
    GET  /sessions/{id}/events      -> {"session_id", "events": [...]}

Each session is a ``d2c.sdk.D2CClient`` bound to one on-disk session; a
message run blocks the request until the turn completes (no streaming
response body) and the resulting loop events are recorded for later
retrieval via the events endpoint.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from d2c import __version__
from d2c.sdk import D2CClient

_MAX_HEADER_BYTES = 16 * 1024
_MAX_BODY_BYTES = 10 * 1024 * 1024  # 10 MB request body cap
_IO_TIMEOUT = 30.0

_REASON = {
    200: "OK",
    201: "Created",
    400: "Bad Request",
    404: "Not Found",
    413: "Payload Too Large",
    431: "Request Header Fields Too Large",
    500: "Internal Server Error",
}

_MESSAGES_RE = re.compile(r"/sessions/([^/]+)/messages")
_EVENTS_RE = re.compile(r"/sessions/([^/]+)/events")


def _event_to_dict(event: Any) -> dict[str, Any]:
    """JSON-safe, redacted view of a d2c.loop event."""
    from d2c.observability import redact

    type_name = type(event).__name__
    if type_name == "TextDelta":
        return {"type": "text_delta", "text": event.text}
    if type_name == "TextResponse":
        return {"type": "text_response", "text": event.text}
    if type_name == "ToolExecutionEvent":
        return {
            "type": "tool_execution",
            "tool_name": event.tool_use.name,
            "tool_input": redact(event.tool_use.input),
            "output": event.result.output,
            "error": event.result.error,
        }
    if type_name == "StopEvent":
        return {"type": "stop", "reason": event.reason}
    return {"type": type_name}


@dataclass
class D2CServer:
    """Minimal local HTTP server. Localhost-only by default."""

    host: str = "127.0.0.1"
    port: int = 8765
    cwd: Path = field(default_factory=Path.cwd)
    model: str | None = None

    def __post_init__(self) -> None:
        self._server: asyncio.Server | None = None
        self._clients: dict[str, D2CClient] = {}
        self._events: dict[str, list[dict[str, Any]]] = {}

    async def start(self) -> None:
        """Bind and start accepting connections (does not block)."""
        self._server = await asyncio.start_server(self._handle_connection, self.host, self.port)

    async def serve_forever(self) -> None:
        if self._server is None:
            await self.start()
        server = self._server
        if server is None:
            raise RuntimeError("server failed to start")
        async with server:
            await server.serve_forever()

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    @property
    def bound_port(self) -> int:
        """Actual bound port — useful when constructed with port=0."""
        if self._server is None or not self._server.sockets:
            return self.port
        return int(self._server.sockets[0].getsockname()[1])

    # ── Connection handling ─────────────────────────────────────────

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=_IO_TIMEOUT)
            if not request_line:
                return
            try:
                method, path, _version = request_line.decode("latin-1").strip().split(" ", 2)
            except ValueError:
                await self._respond(writer, 400, {"error": "malformed request line"})
                return

            headers: dict[str, str] = {}
            total = len(request_line)
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=_IO_TIMEOUT)
                total += len(line)
                if total > _MAX_HEADER_BYTES:
                    await self._respond(writer, 431, {"error": "headers too large"})
                    return
                if line in (b"\r\n", b"\n", b""):
                    break
                key, sep, value = line.decode("latin-1").partition(":")
                if sep:
                    headers[key.strip().lower()] = value.strip()

            body = b""
            try:
                content_length = int(headers.get("content-length", "0") or "0")
            except ValueError:
                content_length = 0
            if content_length > _MAX_BODY_BYTES:
                await self._respond(writer, 413, {"error": "request body too large"})
                return
            if content_length:
                body = await asyncio.wait_for(
                    reader.readexactly(content_length), timeout=_IO_TIMEOUT
                )

            payload: Any = {}
            if body:
                try:
                    payload = json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    await self._respond(writer, 400, {"error": "invalid JSON body"})
                    return

            status, result = await self._route(
                method, path.split("?", 1)[0], payload if isinstance(payload, dict) else {}
            )
            await self._respond(writer, status, result)
        except (asyncio.IncompleteReadError, TimeoutError, ConnectionError):
            pass
        except Exception:
            # Never leak internals (stack traces, secrets) to the client.
            try:
                await self._respond(writer, 500, {"error": "internal server error"})
            except Exception:
                pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    # ── Routing ──────────────────────────────────────────────────────

    async def _route(self, method: str, path: str, payload: dict) -> tuple[int, dict]:
        if method == "GET" and path == "/health":
            return 200, {"status": "ok", "version": __version__}

        if method == "POST" and path == "/sessions":
            return self._create_session(payload)

        m = _MESSAGES_RE.fullmatch(path)
        if method == "POST" and m:
            return await self._send_message(m.group(1), payload)

        m = _EVENTS_RE.fullmatch(path)
        if method == "GET" and m:
            return self._get_events(m.group(1))

        return 404, {"error": f"no route for {method} {path}"}

    def _create_session(self, payload: dict) -> tuple[int, dict]:
        model = payload.get("model") or self.model
        client = D2CClient(cwd=self.cwd, model=model)
        session_id = client.create_session()
        self._clients[session_id] = client
        self._events[session_id] = []
        return 201, {"session_id": session_id}

    async def _send_message(self, session_id: str, payload: dict) -> tuple[int, dict]:
        client = self._clients.get(session_id)
        if client is None:
            return 404, {"error": f"unknown session '{session_id}'"}
        prompt = payload.get("prompt")
        if not prompt or not isinstance(prompt, str):
            return 400, {"error": "'prompt' (string) is required"}

        text_parts: list[str] = []
        stop_reason: str | None = None
        async for event in client.run(prompt, session_id=session_id):
            record = _event_to_dict(event)
            self._events[session_id].append(record)
            if record["type"] == "text_response":
                text_parts.append(record["text"])
            elif record["type"] == "stop":
                stop_reason = record["reason"]

        return 200, {
            "session_id": session_id,
            "text": "".join(text_parts),
            "stop_reason": stop_reason,
        }

    def _get_events(self, session_id: str) -> tuple[int, dict]:
        events = self._events.get(session_id)
        if events is None:
            return 404, {"error": f"unknown session '{session_id}'"}
        return 200, {"session_id": session_id, "events": events}

    # ── Response writing ────────────────────────────────────────────

    @staticmethod
    async def _respond(writer: asyncio.StreamWriter, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        reason = _REASON.get(status, "OK")
        head = (
            f"HTTP/1.1 {status} {reason}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode("latin-1")
        writer.write(head + body)
        await writer.drain()
