# Phase 51: Tool breadth batch 2

**Priority:** HIGH (Highest-ranked backlog candidate)

## Context

Phase 41 expanded built-ins from 17 to 23 with git, filesystem, and structured edit tools. The
backlog reconciliation ranks additional tool breadth as the highest remaining ROI item, especially
tools that reduce risky shell usage and improve deterministic editing.

## Goal

Add a small, high-value second batch of built-in tools:

1. `ApplyPatch` or `MultiEdit` for atomic multi-hunk edits.
2. `EnvInfo` for structured environment/runtime inspection without shelling out.

Keep the batch small and fully tested.

## Scope

In scope:

- 2-3 new built-in tools
- tool schemas and permissions
- Read-before-Write enforcement for write tools
- file-history checkpoints
- `FILE_CHANGED` hooks
- audit events through existing executor paths
- docs/tool inventory update
- tests

Out of scope:

- trying to reach all ~54 paper tools
- browser/computer-use tools
- provider-backed tools
- new permission modes

## Candidate Tools

### `MultiEdit`

Purpose:

- Apply multiple exact replacements to one file in one atomic operation.

Difference from `ReplaceMany`:

- If `ReplaceMany` already covers this completely, do not duplicate it.
- Instead, either improve `ReplaceMany` or skip `MultiEdit`.

Decision point:

```text
If ReplaceMany is already a complete MultiEdit equivalent, mark MultiEdit obsolete and implement ApplyPatch instead.
```

### `ApplyPatch`

Purpose:

- Apply a unified diff patch to one or more files.

Why:

- Familiar coding-agent edit primitive.
- Safer than shelling out to `patch`.
- Can validate paths, prior reads, and atomicity.

Permission:

- `WRITE`
- not concurrent-safe

Safety:

- reject absolute paths unless existing policy allows them
- reject path traversal
- require prior read for modified/deleted existing files
- checkpoint every file before mutation
- apply atomically: if any hunk fails, no file is changed

### `EnvInfo`

Purpose:

- Return structured runtime information:
  - Python version
  - platform
  - cwd
  - git availability
  - package version
  - configured model name
  - WebSearch provider name, not key
  - sandbox enabled flag
  - audit log enabled flag

Why:

- Reduces Bash usage for environment inspection.
- Useful for debugging and model context.

Permission:

- `READ`
- concurrent-safe

Never expose secrets.

## Files to Modify

- `src/d2c/tools/`
- `src/d2c/tools/pool.py`
- `tests/test_phase51_tools.py`
- `tests/test_tools.py`
- `README.md`
- `COMPARISON.md`
- `plans/tool-inventory.md`

## Tests

Add tests for:

1. tool schemas and pool registration
2. `EnvInfo` excludes API keys/secrets
3. `EnvInfo` returns expected structured fields
4. `ApplyPatch` applies a simple patch
5. `ApplyPatch` rejects traversal/absolute paths
6. `ApplyPatch` requires prior read for existing file edits
7. `ApplyPatch` checkpoints before mutation
8. `ApplyPatch` is atomic on failed hunk
9. `ApplyPatch` fires `FILE_CHANGED`
10. output metadata includes changed paths/counts

## Verification

Run:

```bash
pytest tests/test_phase51_tools.py
pytest tests/test_tools.py
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

- At least two high-value tool additions or improvements land.
- No new tool bypasses permission checks.
- Write tools preserve Read-before-Write and checkpoint behavior.
- Tool count/docs are updated.
- Full gate suite remains green.

