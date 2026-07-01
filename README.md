# d2c

A Python re-implementation of the Claude Code agent architecture, built subsystem-by-subsystem from the paper *"Dive into Claude Code."* It reproduces the essential parts of a modern coding agent — the agent loop, tool system, permission gating, context/compaction management, session persistence, subagents, hooks, MCP, and skills — scoped to demonstrate the architectural principles rather than clone the full product.

> **Backend note:** despite the "Claude Code" framing and the `anthropic` SDK dependency, d2c talks to **DeepSeek's Anthropic-compatible API**, not Anthropic. You need a `DEEPSEEK_API_KEY`, not an Anthropic key.

## Install

Requires Python ≥ 3.11.

```bash
pip install -e ".[dev]"
```

## Configure

Set your DeepSeek API key via environment variable or a `.env` file:

```bash
export DEEPSEEK_API_KEY=sk-...
# optional:
export DEEPSEEK_BASE_URL=https://api.deepseek.com/anthropic   # default
export D2C_MODEL=deepseek-v4-pro                              # default
export D2C_SANDBOX=1                                          # sandbox Bash (off by default)

# optional — enable the WebSearch tool (Tavily):
export D2C_WEBSEARCH_PROVIDER=tavily
export D2C_WEBSEARCH_API_KEY=tvly-...
export D2C_WEBSEARCH_TIMEOUT=15                               # seconds (default)
```

Without `D2C_WEBSEARCH_PROVIDER`/`D2C_WEBSEARCH_API_KEY`, the `WebSearch` tool returns a clear
"not configured" error instead of results.

Tavily requires registering for an API key. Its free plan currently includes monthly credits with no
credit card required, which is enough for basic local WebSearch testing.

`.env` resolution: `~/.d2c/.env` always loads; a project-local `.env` loads **only if the workspace is trusted** (see [Workspace trust](#workspace-trust)). Shell environment variables take precedence over `.env` values.

Default model is `deepseek-v4-pro`. Aliases are accepted: `v3`/`chat` → `deepseek-chat`, `r1`/`reasoner` → `deepseek-reasoner`.

## Usage

```bash
python -m d2c                    # interactive REPL (rich prompt_toolkit console)
python -m d2c "fix the bug in foo.py"   # single-shot headless run
python -m d2c --mcp              # run as an MCP server (stdio JSON-RPC) for IDE integration
python -m d2c --list-models
```

Common flags:

| Flag | Description |
|------|-------------|
| `--model <name>` | Model or alias to use |
| `--max-turns <n>` | Max agent turns (default 25) |
| `--cwd <path>` | Working directory |
| `--resume <id>` | Resume a saved session |
| `--fork <id>` | Branch a new session from an existing one |
| `--rewind-files <id>` | Revert all filesystem changes made during a session |
| `--trust` / `--no-trust` | Control the workspace trust gate |

In the REPL, slash commands include `/exit`, `/clear`, `/resume`, `/fork`, `/settings`, and `/help`.

## How it works

Every entry surface — interactive REPL, headless one-shot, and MCP server — converges on a single async agent loop, `queryLoop()` in `src/d2c/loop.py`:

```
assemble context → call model → permission-gate tools → dispatch → shape context → repeat
```

The loop runs until the model returns a text-only response, hits the turn limit, or is stopped. Read-only tools run concurrently; writes and shell commands serialize.

Key subsystems (see [`CLAUDE.md`](./CLAUDE.md) for the full map):

- **Tools** (`tools/`) — 23 built-ins assembled through a single deny-first pool:
  - *files/search*: Read, Write, Edit, Glob, Grep, NotebookEdit, ListDir, FileInfo, ReplaceMany, JsonEdit
  - *shell/git*: Bash, GitStatus, GitDiff
  - *web*: WebFetch, WebSearch
  - *tasks/memory/meta*: TaskCreate/Update/List, Remember, AgentStatus, ToolSearch, and meta-tools Skill + Agent
  - plus any tools contributed by connected **MCP** servers.
- **Permissions** (`permissions/`) — deny-first evaluation with four modes (`plan`, `default`, `acceptEdits`, `dontAsk`); deny rules always win. Includes AST-based shell-command safety analysis.
- **Context management** (`compact.py`) — graduated compaction: tool-result budgeting → LLM summarization → cache-aligned boundaries, with `tiktoken` token counting and prompt-cache breakpoints.
- **Persistence** (`persistence.py`) — append-only JSONL session transcripts with resume/fork; file-history snapshots power `--rewind-files`.
- **Extensibility** — lifecycle hooks (`hooks.py`), a 4-level CLAUDE.md memory hierarchy (`memory.py`), markdown skills (`skills/`), and plugins (`plugins/`).
- **Subagents & background work** — isolated subagents (`subagent.py`) with git-worktree isolation (`worktree.py`) and an optional persistent background agent (`kairos.py`).
- **MCP** (`mcp/`) — both a client (discover external servers, merge their tools) and a server (`--mcp`), over stdio/SSE/HTTP/WebSocket transports.

## Workspace trust

d2c gates project-local features behind a trust decision. In an untrusted workspace it skips project `.env`, plugins, skills, MCP, and memory, and forces `default`/`plan` permission mode. Use `--trust` / `--no-trust` to control it.

## Development

The project was built as a numbered sequence of phases. Each phase has a design doc under [`plans/`](./plans/) and a matching commit. `plans/master_plan.md` is the original blueprint — treat it as historical intent; the code has since diverged.

```bash
pytest                                  # run all tests
pytest tests/test_loop.py               # a single file
pytest tests/test_loop.py::test_name    # a single test
```

Tests are async and use explicit `@pytest.mark.asyncio` markers — mark new async tests accordingly.

The suite runs on a fresh `python -m venv` install (`pip install -e ".[dev]"`) and in CI
(`.github/workflows/tests.yml`, Python 3.11 & 3.13). A wheel built with `python -m build` includes
the bundled skill data (`d2c/skills/*.md`) needed at runtime.

See [`CLAUDE.md`](./CLAUDE.md) for architecture details and conventions.
