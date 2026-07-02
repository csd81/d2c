# Phase 70: Approval management UX

**Priority:** HIGH (small user-facing safety/control improvement)

## Context

Phases 52, 64, and 65 added a mature approval flow:

- `[y]` approves one action
- `[a]` approves the exact action for the current session
- `[A]` persists an exact-match approval across sessions in
  `~/.d2c/approvals.json`
- stored approvals are hashes plus timestamps, not plaintext tool inputs

The remaining rough edge is management. Users can persist approvals but cannot
inspect basic cache state or clear persisted approvals from inside `d2c`. Phase
64 intentionally left this out and told users to delete the file manually.

## Goal

Add a small approval-management surface so users can answer:

1. Where are persistent approvals stored?
2. How many persistent approvals exist?
3. How many session approvals exist?
4. How do I clear session approvals?
5. How do I reset persistent approvals?

## Scope

In scope:

- REPL slash command for approval status/reset
- optional headless CLI command if it fits cleanly
- tests for formatting and reset semantics
- docs/security update
- no plaintext approval disclosure

Out of scope:

- showing original commands/tool inputs
- broad pattern approvals
- editing individual approval entries by content
- team/shared approvals
- expiry policies
- GUI/TUI settings screen

## Proposed UX

Add `/approvals` to the interactive REPL.

Suggested commands:

```text
/approvals
/approvals clear-session
/approvals reset
```

Behavior:

```text
/approvals
  session approvals: 3
  persistent approvals: 12
  path: ~/.d2c/approvals.json
```

```text
/approvals clear-session
  cleared 3 session approval(s)
```

```text
/approvals reset
  reset persistent approval cache at ~/.d2c/approvals.json
```

Keep default behavior non-destructive. `/approvals` should only report status.
Destructive actions require explicit subcommands.

## Design Notes

`ApprovalCache` already has the right primitives:

- exact-match hashes
- persistent path
- runtime/session cache
- `clear()` for runtime only
- `reset()` for runtime + disk

If needed, add small introspection methods:

```python
def runtime_count(self) -> int: ...
def persistent_count(self) -> int: ...
def path(self) -> Path | None: ...
```

Do not expose hash values unless there is a strong reason. Counts and path are
enough for v1.

## Files to Inspect / Modify

Likely:

```text
src/d2c/approvals.py
src/d2c/main.py
docs/security.md
README.md
tests/test_phase64_approvals.py
tests/test_repl_commands.py
```

Optional:

```text
CHANGELOG.md
tests/test_phase70_approvals_ux.py
```

## Tests

Add tests for:

1. `/approvals` prints session count, persistent count, and path.
2. `/approvals clear-session` clears only runtime/session approvals.
3. `/approvals reset` clears runtime and persistent approvals.
4. Unknown subcommands return a helpful error and do not mutate state.
5. Output never includes plaintext commands/tool inputs.
6. Headless/noninteractive behavior is unchanged.

Use temporary approval paths in tests. Do not touch the real
`~/.d2c/approvals.json`.

## Verification

Fast:

```bash
python -m pytest tests/test_phase64_approvals.py tests/test_repl_commands.py
python -m ruff check .
python -m ruff format --check .
python -m mypy
```

Full before push:

```bash
python -m pytest
python -m bandit -c pyproject.toml -r src/d2c
python -m pip_audit || true
rm -rf dist
python -m build
python -m twine check dist/*
```

## Acceptance Criteria

- Users can inspect approval cache state from the REPL.
- Users can clear session approvals without touching persistent approvals.
- Users can reset persistent approvals without manually deleting files.
- Approval output exposes counts/path only, never original commands or tool
  inputs.
- Tests cover status, clear-session, reset, and invalid subcommands.
- Existing gates remain green.

## Expected Outcome

Persistent approvals become controllable instead of opaque. This closes the
main UX gap left by Phase 64 while preserving the conservative exact-match,
hash-only security model.
