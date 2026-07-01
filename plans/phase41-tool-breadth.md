# Phase 41: Tool breadth expansion

**Priority:** MEDIUM (Capability breadth after safety, search, and hooks)

## Context

`d2c` currently has roughly 17 built-in tools plus dynamic MCP tools. The paper describes a much
larger tool surface, up to about 54 tools depending on feature gates.

This difference is intentional so far: `d2c` is an educational Python port, not a full product
clone. Still, after the core safety and integration work, selectively expanding built-in tools can
make the agent more useful and closer to the paper's design.

## Goal

Add tool breadth in a controlled way:

1. Inventory the paper's tool categories.
2. Compare them against current `d2c` tools.
3. Choose a small batch of high-value missing tools.
4. Implement each with permissions, tests, docs, and MCP/tool-pool integration.
5. Keep lower-value or platform-specific tools deferred.

Do not attempt a one-shot jump from 17 tools to 54 tools.

## Scope

In scope:

- tool inventory and gap table
- 3-6 high-value new built-in tools
- permission categories and deny-first rules
- tool schemas
- tests for each new tool
- README/COMPARISON updates

Out of scope:

- duplicating every Claude Code internal tool
- product-specific telemetry tools
- large UI/TUI features
- tools better implemented through MCP servers
- unsafe shell wrappers that bypass permission checks

## Files to Inspect/Modify

1. `src/d2c/tools/`
   - Add new tool modules.
   - Keep one module per tool or tightly related tool family.

2. `src/d2c/tools/pool.py`
   - Register new built-ins.
   - Gate tools where appropriate.

3. `src/d2c/tools/__init__.py`
   - Add runtime accessors only if tools need shared runtime state.

4. `src/d2c/permissions/__init__.py`
   - Add deny/allow behavior only where needed.

5. `tests/`
   - Add focused unit tests per tool.
   - Add tool-pool assembly tests.

6. `README.md`
   - Update tool list.

7. `COMPARISON.md`
   - Update tool count and describe added categories.

## Tool Inventory Step

Create a gap table:

```text
Category | Paper tool(s) | d2c equivalent | Missing? | Priority | Notes
```

Suggested categories:

- file read/write/edit
- search/glob
- shell/process
- web fetch/search
- notebooks
- tasks/todos
- agents/subagents
- memory/skills
- MCP
- diagnostics/status
- git helpers
- project/package helpers
- planning/spec tools
- image/browser/UI tools if applicable

This table can live in this phase file or a separate `plans/tool-inventory.md`.

## Selection Criteria

Pick new tools only if they satisfy most of these:

- commonly useful in coding workflows
- safer or clearer than asking the model to use Bash
- easy to test deterministically
- has an obvious permission category
- works cross-platform or has clean platform guards
- not better delegated to MCP
- does not require secret/provider configuration unless clearly documented

## Recommended First Batch

### 1. GitStatus tool

Purpose:

- Return branch, changed files, staged/unstaged summary.

Why:

- Safer and more structured than `git status` via Bash.
- Helps the model inspect worktree state before edits/commits.

Permission:

- READ

Tests:

- inside git repo
- outside git repo
- staged vs unstaged files

### 2. GitDiff tool

Purpose:

- Return diff for whole repo or specific path.
- Optional staged flag.

Why:

- Safer than Bash.
- Useful before final summaries and reviews.

Permission:

- READ

Tests:

- unstaged diff
- staged diff
- path-specific diff
- output truncation/metadata

### 3. ListDir tool

Purpose:

- Structured directory listing with file type, size, and optional depth.

Why:

- More predictable than `ls`.
- Cross-platform.

Permission:

- READ

Tests:

- hidden files option
- depth limit
- non-existent path
- path outside cwd behavior

### 4. FileInfo tool

Purpose:

- Return metadata for a file/path: exists, type, size, modified time, maybe hash.

Why:

- Lets the agent inspect files without reading large content.

Permission:

- READ

Tests:

- file
- directory
- missing path
- symlink if supported

### 5. ReplaceMany tool

Purpose:

- Apply multiple exact replacements in one file atomically.

Why:

- Reduces repeated Edit calls.
- Safer than `sed -i`.

Permission:

- WRITE

Tests:

- requires prior Read
- all replacements succeed atomically
- if one replacement fails, file is unchanged
- checkpoint is created before mutation

### 6. JsonEdit tool

Purpose:

- Structured JSON modifications by path.

Why:

- Safer than ad hoc string edits for package/config files.

Permission:

- WRITE

Tests:

- set/add/remove
- formatting preservation if supported, or deterministic formatting if not
- invalid JSON
- requires prior Read

## Gating

Use gates where appropriate:

```text
always-on:
  GitStatus, GitDiff, ListDir, FileInfo

write tools:
  ReplaceMany, JsonEdit
  must respect Read-before-Write and file-history checkpoints

experimental:
  any provider-backed or platform-specific tool
```

## Permission Rules

Every tool must declare:

- `PermissionCategory.READ`, `WRITE`, `SHELL`, or `META`
- whether it is concurrent-safe
- whether it needs prior Read
- how it interacts with file-history checkpoints

Avoid adding a tool that bypasses existing safety invariants.

## Tests

Add tests for:

1. Tool schemas include required fields.
2. Tool-pool assembly includes new tools.
3. Permission categories are correct.
4. Read tools are concurrent-safe where appropriate.
5. Write tools are not concurrent-safe.
6. Write tools obey Read-before-Write.
7. Write tools create file-history checkpoints.
8. Output truncation protects the context window.
9. Cross-platform path handling.

Suggested test files:

```text
tests/test_git_tools.py
tests/test_filesystem_tools.py
tests/test_structured_edit_tools.py
tests/test_phase41_tools.py
```

## Verification

Run:

```bash
pytest tests/test_tools.py
pytest tests/test_git_tools.py
pytest tests/test_filesystem_tools.py
pytest tests/test_structured_edit_tools.py
pytest tests/test_phase41_tools.py
pytest
```

## Acceptance Criteria

- A tool inventory exists and explains what is still intentionally deferred.
- At least 3 high-value tools are added with tests.
- No new tool bypasses permission checks.
- Write tools preserve Read-before-Write and checkpoint behavior.
- README tool list is updated.
- COMPARISON tool count and breadth section are updated.

## Expected Outcome

`d2c` gains useful built-in capability without sacrificing the depth gained in earlier phases.
The project moves closer to the paper's breadth while keeping tool additions deliberate, tested, and
safety-gated.
