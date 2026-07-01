# Phase 49: True interactive ASK permission handling

**Priority:** HIGHEST (Human decision authority)

## Context

The permission system can produce an `ASK` decision, but the runtime must handle that decision
explicitly:

- interactive mode should prompt the user
- headless mode should fail safe with a permission-required result
- streaming execution should not speculatively execute
- MCP should return permission-required instead of blocking for terminal input

This is the most important remaining semantic gap versus the paper's human-decision-authority model.
Phase 43 captured the design. Phase 49 implements and verifies it.

## Goal

Make this invariant true everywhere:

```text
ASK never executes without explicit approval.
```

## Scope

In scope:

- approval callback plumbing
- REPL approval prompt
- non-streaming executor behavior
- streaming executor behavior
- headless behavior
- MCP behavior
- audit logging for ask/approve/deny
- tests with side-effect tools
- docs/audit updates

Out of scope:

- persistent approval cache
- "always allow" rules UI
- `bubble` permission mode
- full TUI modal dialog
- remote approval service

## Files to Inspect/Modify

1. `src/d2c/permissions/__init__.py`
   - confirm `ASK` decision shape and reason fields

2. `src/d2c/loop.py`
   - add approval callback to `LoopConfig`
   - update `_execute_one_tool`
   - emit permission-required results when approval is unavailable

3. `src/d2c/streaming_executor.py`
   - await approval callback when available
   - fail safe when unavailable
   - never execute ASK speculatively

4. `src/d2c/main.py`
   - implement interactive prompt callback
   - pass callback in interactive mode
   - do not pass callback in headless mode unless explicitly configured

5. `src/d2c/mcp/server.py`
   - ensure ASK maps to permission-required/error result
   - no terminal prompt from MCP server

6. `src/d2c/observability.py`
   - audit events for permission ask/approved/denied

7. `COMPARISON.md`
   - remove ASK gap only after tests pass

8. `README.md` / `docs/security.md`
   - document interactive vs headless/MCP behavior

9. `tests/test_phase49_ask_permissions.py`
   - new focused tests

## Design

### Approval callback

Add a runtime callback type:

```python
ApprovalCallback = Callable[[PermissionRequest, PermissionResult], Awaitable[bool]]
```

Add to loop/executor config:

```python
approval_callback: ApprovalCallback | None = None
```

Semantics:

```text
True  -> execute exactly once
False -> deny, do not execute
error -> deny, do not execute
None  -> permission-required, do not execute
```

### Shared helper

Create one helper so streaming and non-streaming paths agree:

```python
async def resolve_ask_permission(
    request: PermissionRequest,
    result: PermissionResult,
    approval_callback: ApprovalCallback | None,
) -> PermissionResult:
    ...
```

If the existing permission result type cannot represent "permission required", use a `DENY` result
with a stable reason:

```text
Permission required: interactive approval is not available in this mode.
```

### Interactive prompt

REPL prompt shape:

```text
Permission required for Bash
Reason: ...
Input: command=...
Allow once? [y/N]:
```

Rules:

- default is deny
- `y` / `yes` approves once
- `n` / `no` / empty denies
- sanitize or truncate displayed inputs
- do not print secrets

### Headless behavior

Headless mode:

```text
ASK -> ToolResult(error=True, metadata={"permission_required": True})
```

The model should see that approval is needed and can explain that to the user.

### MCP behavior

MCP mode:

```text
ASK -> JSON-RPC tool result/error indicating permission_required
```

Do not prompt on stdin from MCP server mode.

### Streaming behavior

Streaming tool execution may begin while the model is still generating output. For ASK:

```text
evaluate permission
if ASK:
  wait for approval callback if present
  otherwise return permission-required result
only execute after approval
```

No speculative execution.

## Observability

Emit structured audit events:

```text
permission_ask
permission_approved
permission_denied
permission_required
permission_approval_error
```

Correlate by:

- session id
- turn id
- tool call id
- tool name

Never log raw secrets from tool input.

## Tests

Add `tests/test_phase49_ask_permissions.py` covering:

1. `ASK` with no callback does not execute a side-effect tool.
2. `ASK` with callback returning `False` does not execute.
3. `ASK` with callback returning `True` executes exactly once.
4. callback exception denies execution.
5. headless path returns `permission_required`.
6. streaming path does not execute `ASK` without approval.
7. streaming path executes after approval.
8. MCP server returns permission-required for `ASK`.
9. interactive prompt default/empty response denies.
10. interactive prompt accepts `y`/`yes`.
11. interactive prompt rejects `n`/`no`.
12. audit log records ask/approved/denied without secrets.

Use fake tools with visible side effects to prove execution state.

## Verification

Run:

```bash
pytest tests/test_phase49_ask_permissions.py
pytest tests/test_permissions.py
pytest tests/test_loop.py
pytest tests/test_streaming_executor.py
pytest tests/test_mcp_server.py
pytest tests/test_observability.py
pytest
ruff check .
ruff format --check .
mypy src/d2c
bandit -c pyproject.toml -r src/d2c
pip-audit
python -m build
twine check dist/*
```

Manual smoke:

```bash
python -m d2c
```

Trigger an action that yields `ASK`:

- empty response denies
- `n` denies
- `y` approves once
- denied action does not execute

## Acceptance Criteria

- `ASK` never falls through to execution.
- Interactive REPL can approve or deny once.
- Headless mode fails safe with a clear permission-required result.
- MCP mode fails safe without terminal prompts.
- Streaming and non-streaming behavior match.
- Side-effect tests prove unapproved tools do not run.
- Audit logs record ask/approval/denial safely.
- `COMPARISON.md` no longer lists ASK handling as an open gap.
- Full Phase 45-48 gate suite remains green.

## Expected Outcome

The permission system finally enforces the paper's human-decision-authority model for uncertain
actions. Tools either run under explicit approval or do not run.
