# Phase 56: Tool breadth batch 3

**Priority:** MEDIUM-HIGH (Reduce shell reliance and improve paper fidelity)

## Context

`d2c` has grown to 25 built-in tools plus MCP. Claude Code's analyzed surface is much wider
(`~54` tools, many feature-gated). The next tool batch should focus on tools that are safer and more
structured than asking the model to use Bash.

## Goal

Add a small third batch of high-value built-ins, selected from:

- `ShellOutput` / background command inspection
- `ConfigInfo` / settings inspection
- `PackageInfo` / dependency metadata
- `CodeSymbols` / lightweight symbol listing
- `TodoSummary` / task status summary if not already covered

Keep this batch to 2-4 tools.

## Scope

In scope:

- new read-mostly tools
- permission categories and schemas
- tool-pool registration
- tests and docs
- no secret leakage

Out of scope:

- browser/computer-use tools
- risky shell wrappers
- provider-backed tools
- reaching all 54 tools in one phase

## Candidate Selection Criteria

Prefer tools that:

- avoid shell usage
- are deterministic
- work cross-platform
- are easy to test
- map to common coding workflows
- have clear permission categories

## Recommended First Picks

1. `ConfigInfo`
   - show effective model, cwd, permission mode, sandbox/audit/WebSearch flags
   - hide secrets
   - READ

2. `PackageInfo`
   - inspect `pyproject.toml`, `package.json`, or common dependency manifests
   - summarize package name, scripts, dependencies
   - READ

3. `CodeSymbols`
   - lightweight Python symbol listing using `ast`
   - classes/functions/imports with line numbers
   - READ

4. `ShellOutput`
   - only if background Bash task storage exposes output safely
   - READ/META

## Tests

Add tests for:

- schema and pool registration
- no secrets in `ConfigInfo`
- package manifest parsing
- malformed manifest handling
- Python AST symbol extraction
- output truncation
- permission category correctness

## Verification

Run the full gate suite:

```bash
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

- 2-4 new tools land with tests.
- No new tool bypasses existing safety invariants.
- Tool count/docs are updated.
- Full gate suite remains green.

