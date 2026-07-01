# Phase 40: Remaining hook events

**Priority:** MEDIUM-HIGH (Extensibility and observability)

## Context

`d2c` defines the full hook event surface inspired by the paper, but only a subset of events are
currently fired. Phase 34 wired several important events (`SESSION_START`, `USER_PROMPT_SUBMIT`,
`SUBAGENT_STOP`, `TASK_CREATED`, `TASK_COMPLETED`), but remaining lifecycle hooks are still inert.

This phase makes the hook system more real for plugins, diagnostics, policy, and future UI features.

## Goal

Wire the remaining useful hook events into runtime paths, with tests proving:

1. Events fire at the right time.
2. Events include useful, non-secret payloads.
3. Veto/additional-context behavior is honored only where the hook contract supports it.
4. Hook failures do not crash unrelated flows unless the hook is explicitly part of a blocking
   decision.

## Scope

In scope:

- lifecycle hook firing
- file/session/task observability hooks
- cwd/workspace hooks if the app has a real cwd-changing path
- tests for event order and payloads
- docs/audit updates

Out of scope:

- new plugin framework design
- new hook transport types
- UI/TUI event stream redesign
- external telemetry service
- changing permission semantics beyond hook firing

## Files to Inspect/Modify

1. `src/d2c/hooks.py`
   - Inventory all defined hook events and their intended contracts.
   - Confirm which support veto or additional context.

2. `src/d2c/main.py`
   - Session lifecycle.
   - REPL slash commands.
   - `/clear`, `/resume`, `/fork`.

3. `src/d2c/loop.py`
   - user prompt submit
   - stop/session end
   - compaction
   - model/tool cycle events

4. `src/d2c/tools/*`
   - file-changing tools
   - task tools
   - background/subagent tools

5. `src/d2c/subagent.py`
   - subagent lifecycle.

6. `tests/`
   - Add focused hook-event regression tests, likely `tests/test_phase40_hooks.py`.

7. `COMPARISON.md`
   - Update remaining hook gap after tests pass.

## Event Inventory

Start by creating a table from `HookEvent`:

```text
event name | currently fired? | source location | intended payload | supports veto?
```

Use this inventory to decide which events are meaningful to wire now.

## Candidate Events to Wire

Prioritize events that have a clear runtime source:

### Session lifecycle

- `SESSION_START`
- `SESSION_END`
- session switch/resume/fork events if already defined

Expected payload:

```python
{
    "session_id": "...",
    "cwd": "...",
    "mode": "...",
    "model": "...",
}
```

No secrets.

### Prompt lifecycle

- `USER_PROMPT_SUBMIT`

Already partially wired. Verify:

- headless path
- interactive path
- MCP server path if applicable
- additional context handling
- veto behavior if supported

### File lifecycle

- `FILE_CHANGED`

Fire after successful `Write`, `Edit`, and notebook edits.

Expected payload:

```python
{
    "path": "...",
    "tool": "Write|Edit|NotebookEdit",
    "operation": "write|edit|notebook_edit",
    "session_id": "...",
}
```

Do not include full file contents by default.

### CWD/workspace lifecycle

- `CWD_CHANGED`

Only wire this if there is an actual runtime path that changes cwd. If cwd is immutable after config
load, document this event as intentionally inactive until cwd switching exists.

### Subagent lifecycle

- `SUBAGENT_START`
- `SUBAGENT_STOP`

`SUBAGENT_STOP` was wired in Phase 34. Verify start/stop pair symmetry and error payloads.

### Task lifecycle

- `TASK_CREATED`
- `TASK_UPDATED`
- `TASK_COMPLETED`

Phase 34 wired created/completed. Add update if defined and not fired.

### Compaction lifecycle

- `PRE_COMPACT`
- `POST_COMPACT`

Already likely fired. Verify payload shape and failure isolation.

## Design Rules

### 1. Payloads must be useful but safe

Do include:

- session id
- cwd
- tool name
- file path
- operation
- status
- timestamps if already available

Do not include by default:

- API keys
- environment values
- full file contents
- full prompts unless the event contract already says so

### 2. Hook failures should be isolated

For observability hooks:

```text
hook failure -> log/record result -> continue
```

For permission/policy hooks:

```text
hook denial/veto -> honor contract
```

Do not accidentally let an observability hook become a new crash path.

### 3. Avoid duplicate firing

Events should fire once per logical action.

Examples:

- `FILE_CHANGED` once per successful edit, not once per internal helper call.
- `SESSION_START` once per active session creation/resume/fork.
- `TASK_COMPLETED` once when status transitions into completed, not every time the task is read.

## Tests

Add tests for:

1. Hook inventory: every defined event is categorized as fired or intentionally inactive.
2. `SESSION_START` and `SESSION_END` fire with safe payloads.
3. `/clear`, `/resume`, `/fork` fire appropriate session lifecycle hooks.
4. `FILE_CHANGED` fires after successful Write/Edit and does not fire when the tool fails.
5. `SUBAGENT_START` and `SUBAGENT_STOP` fire as a pair, including error cases.
6. `TASK_UPDATED` fires on task status changes if the event exists.
7. Hook failure in an observability event does not crash the user flow.
8. Sensitive values such as API keys are not present in hook payloads.

Use fake callback hooks to capture payloads.

## Verification

Run:

```bash
pytest tests/test_hooks.py
pytest tests/test_phase40_hooks.py
pytest
```

If filenames differ, run the closest hook, tool, REPL, and subagent tests.

## Acceptance Criteria

- Every hook event is either fired by a tested runtime path or documented as intentionally inactive.
- Remaining unfired hooks have a clear reason, such as no current runtime source.
- Newly wired events have tests for timing and payload shape.
- Hook failures in observability paths do not crash normal execution.
- `COMPARISON.md` no longer says hooks are broadly scaffolded but inert; it distinguishes wired,
  intentionally inactive, and still-open events.

## Expected Outcome

The hook system becomes a real extension surface rather than mostly an enum. This improves plugin
support, debugging, policy integration, and future UI features without adding new user-facing
capability.
