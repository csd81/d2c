# Phase 44: Observability and audit logging

**Priority:** HIGHEST (Production debugging and safety auditability)

## Context

`d2c` now has real depth: permission gates, streaming and non-streaming tool execution, file-history
checkpoints, hooks, compaction, WebSearch, subagents, MCP, and 23 built-in tools.

The next production-grade gap is observability. When a session fails or behaves unexpectedly, the
operator needs to answer:

- what did the model request?
- which tool ran?
- why was permission allowed, denied, or asked?
- what files changed?
- what hooks fired?
- did compaction alter context?
- did WebSearch/provider/model calls fail?
- which session and turn caused the issue?

This phase adds structured, redacted, correlated audit logging.

## Goal

Implement production-useful observability:

1. Structured JSON logs.
2. Session/turn/tool-call correlation.
3. Permission decision audit events.
4. Tool execution timing and result status.
5. File-change audit events.
6. Model call and compaction events.
7. Hook firing/failure events.
8. WebSearch/provider events.
9. Secret redaction.
10. Tests proving logs are useful and safe.

## Scope

In scope:

- structured audit logger
- redaction utilities
- correlation IDs
- log-level config
- local file logs under `~/.d2c/logs` or configured path
- tests for redaction and emitted event shape
- docs for logs and privacy

Out of scope:

- external telemetry/SaaS logging
- OpenTelemetry exporter
- metrics dashboard
- tracing UI
- remote log upload
- storing full prompts by default

## Files to Create/Modify

1. CREATE `src/d2c/observability.py`
   - structured audit logger
   - redaction helpers
   - correlation context helpers

2. MODIFY `src/d2c/config.py`
   - log level
   - audit log enabled flag
   - audit log path
   - optional prompt/content logging flags, default off

3. MODIFY `src/d2c/main.py`
   - initialize observability at startup
   - attach session id/cwd/model/mode context

4. MODIFY `src/d2c/loop.py`
   - model call start/end
   - turn start/end
   - permission decisions
   - non-streaming tool execution events

5. MODIFY `src/d2c/streaming_executor.py`
   - streaming tool execution events
   - permission decisions

6. MODIFY `src/d2c/tools/*`
   - only where tool-specific events are needed, such as WebSearch.
   - Prefer central executor logging over scattering logs across tools.

7. MODIFY `src/d2c/compact.py`
   - compaction start/end/failure events.

8. MODIFY `src/d2c/hooks.py`
   - hook fired/failed events.

9. CREATE `tests/test_observability.py`
   - redaction and event-shape tests.

10. OPTIONAL MODIFY `README.md`
    - document audit logging config.

## Configuration

Suggested env vars:

```bash
D2C_LOG_LEVEL=INFO
D2C_AUDIT_LOG=1
D2C_AUDIT_LOG_PATH=~/.d2c/logs/audit.jsonl
D2C_LOG_PROMPTS=0
D2C_LOG_TOOL_OUTPUTS=0
```

Defaults:

```text
log level: INFO
audit log: enabled for local file? choose conservative default
prompt logging: off
tool output logging: off
```

Recommendation:

- enable structured audit events by default if they are metadata-only and redacted
- keep full prompt/tool-output logging off by default

## Event Schema

Each event should be one JSON object per line:

```json
{
  "ts": "2026-07-01T19:00:00.000Z",
  "level": "INFO",
  "event": "tool_call_end",
  "session_id": "...",
  "turn_id": 3,
  "tool_call_id": "...",
  "tool_name": "Read",
  "duration_ms": 12,
  "status": "ok",
  "error": false
}
```

Common fields:

- `ts`
- `level`
- `event`
- `session_id`
- `turn_id`
- `tool_call_id`
- `tool_name`
- `cwd`
- `model`
- `permission_mode`
- `duration_ms`
- `status`
- `error`

Do not require every event to populate every field.

## Core Events

### Session

```text
session_start
session_end
session_resume
session_fork
```

### Turn / model

```text
turn_start
turn_end
model_call_start
model_call_end
model_call_error
output_token_recovery_retry
```

### Tool execution

```text
tool_call_start
tool_call_end
tool_call_error
tool_call_skipped
```

Include:

- tool name
- tool call id
- duration
- error boolean
- output length, not full output by default

### Permission

```text
permission_decision
permission_denied
permission_ask
permission_approved
permission_error
```

Include:

- tool name
- category
- decision
- reason
- rule id/name if available

### File changes

```text
file_changed
file_checkpoint_created
file_rewind
```

Include:

- path
- operation
- tool name
- checkpoint id/path if safe

Do not include full file contents.

### Compaction

```text
compaction_start
compaction_end
compaction_error
```

Include:

- shaper name
- pre message count
- post message count
- estimated tokens before/after

### Hooks

```text
hook_fired
hook_result
hook_failed
```

Include:

- hook event name
- hook type
- duration
- veto/additional_context flags, not full injected content by default

### WebSearch

```text
websearch_request
websearch_result
websearch_error
```

Include:

- provider
- max_results
- result_count
- domain filter count
- error class

Do not log API key.

## Redaction

Implement a central redaction function:

```python
def redact(value: Any) -> Any:
    ...
```

Must redact:

- `DEEPSEEK_API_KEY`
- `D2C_WEBSEARCH_API_KEY`
- `Authorization`
- `X-Api-Key`
- `X-Subscription-Token`
- values matching `sk-...`
- values matching `tvly-...`
- `.env` contents
- common tokens/password fields

Redaction output:

```text
[REDACTED]
```

For long strings, truncate after redaction-safe inspection:

```text
first 500 chars + "... [truncated]"
```

## Correlation IDs

Use:

- session id from `SessionStore`
- monotonically increasing turn id from `LoopState.turn_count`
- tool call id from model tool call id
- generated event id if useful

Every permission/tool event should share the same `tool_call_id`.

## Tests

Add tests for:

1. Redacts DeepSeek-style keys.
2. Redacts Tavily-style keys.
3. Redacts Authorization-like headers.
4. Writes JSONL events with required fields.
5. Tool call start/end events include same `tool_call_id`.
6. Permission decision event includes decision/reason and no secrets.
7. WebSearch auth error logs provider/error but not API key.
8. Hook failure logs `hook_failed` but does not crash hook execution.
9. Full tool output is not logged by default.
10. Prompt text is not logged by default.

## Verification

Run:

```bash
pytest tests/test_observability.py
pytest tests/test_phase43_ask_permissions.py
pytest tests/test_web_search.py
pytest tests/test_hooks.py
pytest
```

Manual smoke:

```bash
D2C_AUDIT_LOG=1 D2C_LOG_LEVEL=DEBUG python -m d2c --max-turns 1 "say hello"
tail -n 20 ~/.d2c/logs/audit.jsonl
```

Verify:

- JSONL parses
- events have session/turn correlation
- no API keys are present

## Acceptance Criteria

- Audit log emits structured JSONL.
- Tool calls, permission decisions, model calls, hooks, compaction, and WebSearch have useful events.
- Events are correlated by session/turn/tool call.
- Full prompts and tool outputs are not logged by default.
- Redaction tests cover known secret shapes.
- Logs are useful enough to debug a failed session without reading the whole transcript.
- README documents observability config and privacy defaults.

## Expected Outcome

Operators can debug and audit agent behavior from structured logs without leaking secrets. This moves
`d2c` toward production-grade reliability while preserving local-first privacy.
