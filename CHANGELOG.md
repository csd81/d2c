# Changelog

All notable changes to d2c are documented here. This project follows a simple
[Keep a Changelog](https://keepachangelog.com/) style.

## Unreleased

- **Phase 65:** the REPL's bare-text `Allow? [y/N/a]:` permission prompt is now a styled, color-coded
  dialog (`prompt_toolkit`, no new dependency): category-colored header, Bash commands risk-colored
  via the existing `acceptEdits` classifier, and Edit/Write/ApplyPatch get a `+N / -M` diff summary
  (short diffs shown inline, longer ones behind a `[d]` expand action) — computed only from the
  already-provided tool input, never a speculative disk read. The approval scopes are now `[y]` once,
  `[a]` session (in-memory only, not persisted), `[A]` always (persisted, splitting out what Phase 64
  called `a`), `[n]` deny.
- **Phase 64:** approval cache (now `A` / "always") persists across sessions and process restarts to
  `~/.d2c/approvals.json` (SHA-256 hashes + timestamps only, atomic writes). `/clear`, `/resume`, and
  `/fork` still reset the in-memory view for the current session; the persisted file is untouched.

## 0.1.0 — 2026-07-01

First packaged release. A Python re-implementation of the Claude Code agent
architecture (DeepSeek backend), built subsystem-by-subsystem.

### Core
- Async agent loop (`queryLoop`) with concurrent-safe tool partitioning,
  streaming execution, output-token recovery, and reactive/proactive compaction.
- Five-layer context compaction with tiktoken accounting and cache-aligned
  boundaries.
- Append-only JSONL session persistence with resume/fork and file-history
  checkpoints (`--rewind-files`).

### Safety
- Deny-first permission engine with an AST shell classifier and 6 modes.
- `acceptEdits` shell hardening (structural classification, not first-word).
- Interactive `ASK` handling; permission gate fails closed.
- Workspace trust gate; security regression suite (path/symlink/shell/redaction/
  trust boundaries) documented in `docs/security.md`.

### Tools (23 built-ins + MCP)
- Read/Write/Edit/Glob/Grep/NotebookEdit/ListDir/FileInfo/ReplaceMany/JsonEdit,
  Bash/GitStatus/GitDiff, WebFetch/WebSearch (Tavily), Task tools, Remember,
  AgentStatus, ToolSearch, and meta-tools Skill/Agent.

### Extensibility & ops
- 27 hook events (19 fired), memory hierarchy, skills, plugins, MCP client +
  server, subagents with worktree isolation.
- Structured, redacted audit logging (`observability.py`).
- `--doctor` diagnostics; `--version`.

### Tooling
- CI quality gates: ruff, mypy (staged), bandit, pip-audit, pytest, build.
