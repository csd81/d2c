# Changelog

All notable changes to d2c are documented here. This project follows a simple
[Keep a Changelog](https://keepachangelog.com/) style.

## Unreleased

- **Phase 68:** first eval-guided tool-tuning pass, using the Phase 67 corpus as
  the measurement loop. Adds an advisory `expect.tolerate_verification_failure`
  flag so a task whose only failure is a trailing verification tool error (e.g.
  running a whole suite that trips an unrelated known-failing fixture test) is
  scored successful, surfacing the swallowed error as a `note` (new
  `EvalTaskResult.notes`) rather than hiding it — this fixes the Phase 67
  `add-test-coverage` false negative (12/13 → 13/13). Sharpens the `ApplyPatch`
  description to steer coordinated multi-file edits/renames toward it and single
  edits toward `Edit`; a focused 6×-per-description A/B on the cross-file rename
  task moved `ApplyPatch` adoption from 0/6 to 3/6 with no success/turn/cost
  regression. Results in `eval/phase68-results.md`.
- **Phase 67:** a checked-in eval corpus (`eval/corpus.yaml`, 13 deterministic tasks) and tiny
  fixture repos (`eval/fixtures/`) make the Phase 66 harness actionable — `eval/README.md`
  documents how to run it and `eval/baseline.md` records a measured baseline (tool-use
  distribution, turns, cost) to guide Phase 68's tool-description tuning. `tests/test_eval_corpus.py`
  validates corpus hygiene (unique IDs, fixture paths, advisory-key shape) without live model calls.
  Also scopes pytest's default collection to `tests/` (`[tool.pytest.ini_options] testpaths`) so the
  fixtures' own throwaway `test_*.py` (including one intentionally-failing test used as an eval
  target) isn't picked up by the real suite.
- **Phase 66:** a headless eval harness (`python -m d2c eval corpus.yaml --out-dir
  ./eval-results`) runs a YAML corpus of task prompts through `d2c.sdk.D2CClient`
  sequentially and reports, per task, turn count, tool-call distribution, token/cost
  usage, compaction events, tool sequence, and outcome — plus a `divergences` list
  against the corpus's advisory `expect` field (never a pass/fail assertion). Adds
  `compaction_shaper_applied` audit events to the snip/microcompact/context-collapse
  shapers (`auto_compact` already audited) so compaction activity is fully observable,
  not just the last-resort model-generated summary.
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
