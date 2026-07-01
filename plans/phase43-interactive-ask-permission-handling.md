# Phase 43: Interactive ASK permission handling

**Priority:** HIGHEST (Human decision authority and safety semantics)

## Context

The permission engine can return an `ASK` decision, but the executor paths do not yet have a true
interactive approval flow. This leaves an important semantic gap: uncertain actions should not execute
automatically, but they also should not be treated the same as hard denials in an interactive session.

The paper emphasizes human decision authority: the user should be able to approve, reject, or audit
actions. This phase makes that behavior real for `ASK`.

## Goal

Implement correct `ASK` handling across runtime surfaces:

1. Interactive REPL: prompt the user for approval before executing an `ASK` tool request.
2. Headless mode: do not execute; return a structured permission-required result.
3. Streaming executor: do not execute until approval is available; fail safe where approval cannot be
   collected.
4. MCP server mode: return a permission-required tool error instead of executing.
5. Tests prove ASK never falls through to automatic execution.

## Scope

In scope:

- `ASK` decision handling
- interactive approval callback/prompt
- executor behavior in streaming and non-streaming paths
- headless/MCP fallback behavior
- transcript/session recording of approval/denial decisions where appropriate
- tests with side-effect tools

Out of scope:

- new permission modes
- `bubble` mode
- long-lived approval cache UI
- full TUI permission dialog design
- policy engine rewrite

## Files to Inspect/Modify

1. `src/d2c/permissions/__init__.py`
   - Confirm `PermissionDecision` / `PermissionResult` shape.
   - Confirm how `ASK`, `ALLOW`, and `DENY` are represented.

2. `src/d2c/loop.py`
   - Non-streaming tool execution path.
   - Permission result handling in `_execute_one_tool`.
   - Add approval callback support to `LoopConfig` if needed.

3. `src/d2c/streaming_executor.py`
   - Streaming tool execution path.
   - Ensure `ASK` does not execute automatically.

4. `src/d2c/main.py`
   - Interactive REPL prompt implementation.
   - Headless behavior.
   - Session setup for approval callback.

5. `src/d2c/mcp/server.py`
   - Tool-call behavior when approval would be required.

6. `tests/`
   - Add `tests/test_phase43_ask_permissions.py`.
   - Update existing permission/loop/streaming/MCP tests if needed.

## Design

### 1. Add an approval callback

Add an optional callback to runtime config:

```python
ApprovalCallback = Callable[[PermissionRequest, PermissionResult], Awaitable[bool]]
```

Possible placement:

```python
@dataclass
class LoopConfig:
    ...
    approval_callback: ApprovalCallback | None = None
```

Semantics:

- returns `True`: execute the tool
- returns `False`: deny the tool
- raises: deny the tool and return a clear error

### 2. Shared ASK resolver

Avoid duplicating logic in multiple executors. Add a helper:

```python
async def resolve_permission_decision(
    request: PermissionRequest,
    result: PermissionResult,
    approval_callback: ApprovalCallback | None,
) -> PermissionResult:
    ...
```

Behavior:

```text
ALLOW -> allow
DENY  -> deny
ASK + callback approves -> allow
ASK + callback rejects  -> deny
ASK + no callback       -> permission_required / deny-safe
ASK + callback error    -> deny-safe
```

If the existing permission model has only `ALLOW/DENY/ASK`, represent `permission_required` as a
denial with a clear reason:

```text
Permission required: interactive approval is not available in this mode.
```

### 3. Interactive prompt

In REPL mode, implement a small prompt:

```text
Allow Bash command?
  Tool: Bash
  Reason: ...
  Input: ...
[y/N]:
```

Rules:

- default is no
- `y`/`yes` approves once
- `n`/`no`/empty denies
- never print secrets from tool input if the tool marks fields sensitive
- keep prompt concise

Start with one-shot approval only. Persistent "always allow" can be a later phase.

### 4. Headless behavior

In headless mode:

```text
ASK -> do not execute -> return ToolResult(error=True, permission_required=True)
```

The model/user should receive enough information to understand that explicit approval is needed.

### 5. MCP behavior

MCP tools should not block waiting for terminal input unless the MCP protocol path has an approval
mechanism. For now:

```text
ASK -> return JSON-RPC tool error / permission-required ToolResult
```

Do not execute automatically.

### 6. Streaming behavior

Streaming starts tool execution during model output. That makes `ASK` trickier.

For Phase 43:

- if an approval callback exists and is safe to await, await it before execution
- if no callback exists, return permission-required result
- do not execute the tool speculatively

This may reduce streaming latency for ASK tools, but preserves safety.

## Transcript / Audit Logging

Record permission outcomes where existing persistence supports it:

- approved by user
- denied by user
- permission required but no approval channel
- approval callback error

Do not store secrets or full sensitive command payloads beyond what the transcript already records.

## Tests

Add tests proving:

1. `ASK` with no approval callback does not execute a side-effect tool.
2. `ASK` with callback returning `False` does not execute.
3. `ASK` with callback returning `True` executes exactly once.
4. Callback exception denies execution.
5. Headless `ASK` returns permission-required/denied result.
6. Streaming executor does not execute `ASK` without approval.
7. Streaming executor executes after approval callback returns `True`.
8. MCP tool call with `ASK` returns a permission-required error.
9. Interactive prompt defaults to deny on empty input.
10. Interactive prompt accepts `y`/`yes` and rejects `n`/`no`.

Use fake side-effect tools so tests can prove execution happened or did not happen.

## Verification

Run:

```bash
pytest tests/test_phase43_ask_permissions.py
pytest tests/test_permissions.py
pytest tests/test_loop.py
pytest tests/test_streaming_executor.py
pytest tests/test_mcp_server.py
pytest tests/test_repl_commands.py
pytest
```

Manual smoke:

```bash
python -m d2c
```

Trigger an action that returns `ASK` and verify:

- prompt appears
- empty response denies
- `y` approves once
- denial is reported back cleanly

## Acceptance Criteria

- `ASK` never falls through to automatic execution.
- Interactive REPL can approve or deny `ASK` actions.
- Headless and MCP modes fail safe with a clear permission-required result.
- Streaming and non-streaming executors behave consistently.
- Side-effect tests prove denied/unapproved tools do not run.
- `COMPARISON.md` no longer lists non-interactive `ASK` as an open gap.

## Expected Outcome

The permission system better matches the paper's human-decision-authority model. Uncertain actions
are either explicitly approved by the user or safely blocked, rather than silently executing.
