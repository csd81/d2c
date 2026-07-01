# Phase 59: SDK, daemon, and server surfaces

**Priority:** MEDIUM (Integration surface)

## Context

`d2c` currently exposes a CLI and MCP server. Claude Code-like systems also expose SDK/server/daemon
entry points for IDEs, automation, and long-running integrations.

## Goal

Add clean programmatic and service entry points:

1. a small Python SDK API
2. optional HTTP server mode
3. groundwork for a local daemon

## Scope

In scope:

- stable Python API wrapper around `queryLoop`
- typed request/response models
- optional local HTTP server with health and session endpoints
- tests

Out of scope:

- public cloud service
- auth system
- multi-user tenancy
- remote code execution service
- background KAIROS

## SDK Shape

Example:

```python
from d2c.sdk import D2CClient

client = D2CClient(cwd=".")
async for event in client.run("summarize this repo"):
    ...
```

## Server Shape

Optional:

```bash
python -m d2c --serve --host 127.0.0.1 --port 8765
```

Endpoints:

- `GET /health`
- `POST /sessions`
- `POST /sessions/{id}/messages`
- `GET /sessions/{id}/events`

Keep localhost-only by default.

## Tests

Add tests for:

- SDK run wrapper
- health endpoint
- local-only default binding
- no API keys in responses
- session creation/message flow with mocked model

## Acceptance Criteria

- SDK API exists and is documented.
- Server mode is optional and localhost-only by default.
- Existing CLI/MCP behavior unchanged.
- Full gate suite remains green.

