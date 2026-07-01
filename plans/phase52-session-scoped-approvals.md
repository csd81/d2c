# Phase 52: Session-scoped persistent approvals

**Priority:** HIGH (Safety usability after ASK handling)

## Context

Phase 49 made `ASK` safe: uncertain actions require explicit approval or fail safe. That approval is
one-shot. The next improvement is session-scoped approval caching so users can reduce repeated prompts
without persisting risky grants across sessions.

## Goal

Add interactive approval choices:

```text
[y] allow once
[n] deny
[a] always allow this exact action for this session
```

Approvals must be in-memory only and must not survive resume/fork/restart.

## Scope

In scope:

- in-memory approval cache
- exact-action matching
- REPL prompt update
- audit logging
- tests
- docs/security update

Out of scope:

- persistent approvals
- broad pattern approvals
- "always allow all Bash"
- UI/TUI permission modal
- team/shared approvals

## Design

Approval cache key should be conservative:

```text
permission mode
tool name
permission category
normalized tool input hash
cwd/session id
```

For Bash, include the exact command string. Do not generalize shell commands.

Behavior:

- cache applies only in the active session
- `/clear`, `/resume`, `/fork` clears approvals
- process restart clears approvals
- cached approval emits audit event

## Files to Modify

- `src/d2c/main.py`
- `src/d2c/loop.py`
- `src/d2c/streaming_executor.py`
- `src/d2c/observability.py`
- `docs/security.md`
- `tests/test_phase52_approvals.py`

## Tests

Add tests for:

1. `a` stores approval for exact action
2. same action later executes without prompting
3. different command/input still prompts
4. `/clear` clears approval cache
5. `/resume` and `/fork` clear approval cache
6. approval cache is not persisted to transcript
7. cached approval logs `permission_approved_cached`
8. secrets are not stored in plain text if avoidable; use hashes for cache keys

## Verification

Run:

```bash
pytest tests/test_phase52_approvals.py
pytest tests/test_phase49_ask_permissions.py
pytest
ruff check .
ruff format --check .
mypy src/d2c
bandit -c pyproject.toml -r src/d2c
pip-audit
python -m build
twine check dist/*
```

## Acceptance Criteria

- Session-scoped approvals work only for exact repeated actions.
- Approvals are never persisted.
- Session switches clear approvals.
- Audit logs distinguish one-shot and cached approvals.
- Full gate suite remains green.

