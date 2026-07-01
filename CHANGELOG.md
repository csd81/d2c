# Changelog

All notable changes to d2c are documented here. This project follows a simple
[Keep a Changelog](https://keepachangelog.com/) style.

## Unreleased

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
