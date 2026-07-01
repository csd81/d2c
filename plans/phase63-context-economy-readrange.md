# Phase 63: Context economy and ReadRange

**Priority:** HIGH (Reduce token cost and improve agent efficiency)

## Context

`d2c` already has strong context-management infrastructure:

- five-layer compaction
- usage/cost accounting
- small inspection tools (`Grep`, `CodeSymbols`, `FileInfo`, `GitDiff`, `ListDir`, etc.)
- tool-output caps

The next improvement is to make context economy explicit in tool guidance and add a line-range read
tool so the agent can avoid dumping whole files when it only needs a section.

## Goal

Reduce unnecessary context growth:

1. Add `ReadRange` / line-range reading.
2. Encourage lightweight inspection before full-file reads.
3. Preserve Read-before-Write safety semantics.
4. Add tests proving range reads are capped, precise, and safe.

## Scope

In scope:

- new `ReadRange` tool or equivalent extension to `Read`
- system prompt/tool guidance update
- Read-before-Write integration
- output truncation/caps
- tests
- docs

Out of scope:

- semantic code indexing
- embeddings
- LSP integration
- automatic model-side tool planning
- changing compaction architecture

## Tool Design

Recommended new tool:

```text
ReadRange
```

Inputs:

```json
{
  "file_path": "src/d2c/loop.py",
  "start_line": 120,
  "end_line": 180,
  "include_line_numbers": true
}
```

Behavior:

- `start_line` and `end_line` are 1-based inclusive.
- clamp maximum lines, for example 300 lines.
- reject invalid ranges.
- reject directories and missing files.
- preserve existing path safety/canonicalization.
- mark the file as read for Read-before-Write.

Permission:

```text
READ
concurrent-safe
```

Output:

```text
src/d2c/loop.py:120-180
120 | ...
121 | ...
```

Metadata:

```python
{
    "file_path": "...",
    "start_line": 120,
    "end_line": 180,
    "returned_lines": 61,
    "total_lines": 900,
    "truncated": false
}
```

## Guidance Update

Update system/tool guidance:

```text
Prefer lightweight inspection tools before full reads:
Grep, Glob, ListDir, FileInfo, CodeSymbols, GitDiff, GitStatus.
Use ReadRange when you know the relevant line range.
Use full Read only when the whole file is genuinely needed.
```

Keep this concise so the system prompt does not grow too much.

## Safety

`ReadRange` should call the same path normalization/read-tracking path as `Read`.

Important invariant:

```text
ReadRange(file) satisfies Read-before-Write for that canonical file.
```

This allows:

```text
ReadRange src/app.py lines 20-80
Edit src/app.py
```

But does not allow symlink/path-spelling bypasses.

## Tests

Add tests for:

1. reads requested line range
2. includes line numbers when requested
3. omits line numbers when requested
4. invalid range rejected
5. range over max lines is clamped/truncated
6. missing file returns clean error
7. directory path returns clean error
8. marks canonical file read
9. ReadRange then Edit succeeds
10. alternate symlink/path spelling cannot bypass canonicalization
11. tool appears in pool and is READ/concurrent-safe
12. system prompt mentions lightweight-first guidance

## Verification

Run:

```bash
pytest tests/test_phase63_readrange.py
pytest tests/test_tools.py
pytest tests/test_security_regressions.py
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

- `ReadRange` exists and is registered.
- It returns only the requested/capped file section.
- It marks files read using canonical path tracking.
- It does not weaken Read-before-Write or symlink protections.
- Tool guidance encourages lightweight inspection before full reads.
- Full gate suite remains green.

## Expected Outcome

The agent can inspect code with much less context overhead, reducing cost and improving long-session
reliability without changing the core loop.
