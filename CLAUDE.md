# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`d2c` is a Python re-implementation of the Claude Code agent architecture, built subsystem-by-subsystem from the paper *"Dive into Claude Code"* (2604.14228v1.pdf). It reproduces the core agent loop, tool system, permission gating, context/compaction management, session persistence, subagents, hooks, MCP, and skills — scoped to demonstrate the architectural principles rather than clone the full product.

**Backend is DeepSeek, not Anthropic.** Despite the `anthropic` SDK dependency and "Claude Code" framing, the agent talks to DeepSeek's Anthropic-compatible API (`base_url=https://api.deepseek.com/anthropic`). You need `DEEPSEEK_API_KEY` set (env var or `.env`), *not* an Anthropic key. Default model is `deepseek-v4-flash`; the stronger `deepseek-v4-pro` is available via `--model pro` (aliases `flash`/`v4-flash` and `pro`/`v4`/`v4-pro` are mapped in `config.py`).

## Commands

```bash
pip install -e ".[dev]"      # install with dev deps (pytest, pytest-asyncio)

python -m d2c                 # interactive REPL (prompt_toolkit TUI)
python -m d2c "a prompt"      # single-shot headless run
python -m d2c --mcp           # run as an MCP server (stdio JSON-RPC) for IDE integration
python -m d2c --resume <id>   # resume a session; --fork <id> to branch one
python -m d2c --list-models

pytest                        # run all tests
pytest tests/test_loop.py     # single file
pytest tests/test_loop.py::test_name   # single test
```

Tests are async and use explicit `@pytest.mark.asyncio` markers (there is no `asyncio_mode=auto`) — mark new async tests accordingly. There is no configured linter or build step beyond the setuptools package build.

## Development model: phases + plans

The project was built as a numbered sequence of ~33 phases, each with a design doc in `plans/` (e.g. `plans/phase26-prompt-caching.md`) and a matching commit (`git log` shows "Phase N: ..."). `plans/master_plan.md` is the original blueprint — **treat it as historical intent, not current truth**; the real code has diverged (added `permissions/`, `plugins/`, `skills/`, `mcp/transports/`, and many tools not in the plan). When touching a subsystem, read its phase plan for rationale, but verify against the actual source. Tests are named both by subsystem (`test_memory.py`) and by phase (`test_phase24.py`).

## Architecture

Everything converges on one async agent loop; subsystems plug into it.

- **`loop.py` — `queryLoop()`** is the center: an async generator running `assemble context → call model → permission-gate tools → dispatch → shape context → repeat` until the model returns text-only, hits `max_turns`, or is stopped. It yields stream events (`TextDelta`, `TextResponse`, `ToolExecutionEvent`, `StopEvent`). The DeepSeek client (`anthropic.AsyncAnthropic` pointed at the DeepSeek base URL) is constructed here. Tool calls are partitioned so `is_concurrent_safe` (read-only) tools run in parallel while writes/shell serialize; results are emitted in original order.
- **`main.py`** is the entry point / CLI dispatch. Both interactive REPL and headless paths build a `LoopConfig` and feed `queryLoop()`. Also hosts the `--mcp` server mode and slash-command handling (`/exit`, `/resume`, `/fork`, `/help`, ...).
- **`context.py`** assembles the system prompt + memoized system context (git status, cwd, platform, date) and prepends CLAUDE.md/user context as a *user* message (not system prompt).
- **`config.py`** — `Config.load()` resolves settings once at startup (immutable thereafter): DeepSeek model/key/base-url, `.env` loading, permission mode, compaction thresholds. **Project `.env` is only loaded if the workspace is trusted** (see trust gate below); `~/.d2c/.env` always loads.

### Tools (`tools/`)
`tools/__init__.py` defines the `Tool` ABC, `ToolResult`, `ToolUse`, and `PermissionCategory` (READ/WRITE/SHELL/META). `tools/pool.py` `assembleToolPool()` is the single source of truth: enumerate built-ins + skills + MCP tools → `is_enabled()` filter → deny-rule pre-filter (forbidden tools are hidden from the model, not just blocked at runtime) → dedupe by name (MCP/extra override built-ins). `tools/tool_search.py` supports deferred/lazy tool schemas. Note `pool.py` defines its *own* `Config`/`Rule` distinct from `config.Config` — don't conflate them.

### Permissions (`permissions/`)
Deny-first evaluation with 4 modes (`plan`, `default`, `acceptEdits`, `dontAsk`). **Deny rules always win, even under `dontAsk`.** Denial is a routing signal fed back to the model, not a hard stop. `permissions/classifier.py` does AST-based command safety analysis for shell commands.

### Context management (`compact.py`)
Graduated compaction pipeline: per-tool-result budget capping → LLM-based summarization (async shapers) → cache-aligned compaction boundaries (kept on prompt-cache breakpoints to preserve cache hits). Prompt caching uses Anthropic `cache_control` breakpoints. BPE token counting via `tiktoken` (`cl100k_base`). Compaction boundaries are recorded in the session transcript for reconstruction on resume.

### Persistence (`persistence.py`, `file_history.py`, `history.py`)
Append-only JSONL session transcripts under `~/.d2c/sessions/` (gitignored). Resume/fork rebuild the message array from the transcript but **do not restore session-scoped permissions**. `file_history.py` snapshots file writes/edits for `--rewind-files` (revert a session's filesystem changes). `history.py` is the global prompt history.

### Extensibility
- **`hooks.py`** — lifecycle event registry (27 events; `PreToolUse`, `PostToolUse`, `Stop`, `PreCompact`, `SessionStart/End`, `Setup`, ...). Hooks can deny/modify tool input, veto stops, and inject context. Types: command/prompt/http/callback.
- **`memory.py`** — 4-level CLAUDE.md hierarchy (managed → user → project → local), `@include` directives, lazy loading of nested-dir memory on file access, and auto-memory writing.
- **`skills/`** — markdown skill definitions with YAML frontmatter (`loader.py`); invoked via `SkillTool`.
- **`plugins/`** — discovered plugins register hooks, skills, and agents at startup (`loader.py`, `manifest.py`).
- **`path_rules.py`** — path-scoped rules.

### Subagents & background work
- **`subagent.py`** + `tools/agent_tool.py` — delegate isolated tasks to subagents with their own context; sidechain transcripts.
- **`worktree.py`** — git-worktree isolation so parallel subagents don't collide.
- **`kairos.py`** — persistent background agent (feature-gated, off by default via `kairos_enabled`).
- **`streaming_executor.py`** — streaming tool execution.
- **`tools/task_tools.py`** — task create/list/update tooling.

### MCP (`mcp/`)
Both client and server. `mcp/client.py` + `mcp/discovery.py` discover external MCP servers and merge their tools into the pool. `mcp/server.py` (+ `--mcp` flag) exposes d2c itself as an MCP server. `mcp/transports/` implements stdio, sse, http, websocket.

### Sandboxing & trust
- **`sandbox.py`** — shell command sandboxing.
- **`trust.py`** — workspace trust gate (global singleton via `get_trust_gate()`/`set_trust_gate()`). Untrusted workspaces skip project-local `.env`, plugins, skills, MCP, and memory, and force `default`/`plan` permission mode. `--trust`/`--no-trust` flags control it. **Tests reset the gate via the autouse `reset_trust` fixture** and use `trusted_gate`/`untrusted_gate` fixtures — respect this when writing tests that touch trust-gated code.

## Conventions

- Async throughout; the loop and all tool `execute()` methods are `async`.
- Function/symbol names mirror the paper's TypeScript (`queryLoop`, `assembleToolPool`, `buildPostCompactMessages`) — keep this camelCase-for-paper-concepts style when extending, even though surrounding Python is snake_case.
- Write/Edit tools require the file to have been Read first in the session (safety check); tests reset this tracking via the autouse `reset_read_files` fixture in `conftest.py`.
- Paths passed to Read/Write/Edit must be absolute.
