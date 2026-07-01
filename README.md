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

# optional — cost accounting (Phase 55; costs are ESTIMATES, not invoices):
export D2C_PRICING_INPUT_PER_MILLION=0.56                     # USD/M input tokens
export D2C_PRICING_OUTPUT_PER_MILLION=1.68                    # USD/M output tokens
export D2C_PRICING_CACHE_READ_PER_MILLION=0.07                # USD/M cache-read tokens
export D2C_DISABLE_COST_ESTIMATES=1                           # track tokens, skip cost math

# optional — structured audit logging (off by default):
export D2C_AUDIT_LOG=1                                        # enable JSONL audit log
export D2C_AUDIT_LOG_PATH=~/.d2c/logs/audit.jsonl            # default
export D2C_LOG_LEVEL=INFO                                     # DEBUG|INFO|WARNING|ERROR
export D2C_LOG_PROMPTS=0                                      # log full prompts (privacy: off)
export D2C_LOG_TOOL_OUTPUTS=0                                # log full tool outputs (privacy: off)
```

**Audit logging** (opt-in) writes one redacted JSON object per line correlated by
`session_id` / `turn_id` / `tool_call_id` — session, model-call, tool-call, permission, file-change,
compaction, hook-failure, and WebSearch events. Secrets (API keys, `Authorization`, `sk-…`/`tvly-…`
shapes) are redacted; full prompts and tool outputs are **not** logged unless you opt in.

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
python -m d2c --doctor           # diagnose config/env (PASS/WARN/FAIL); add --json or --doctor-live
python -m d2c --version          # print version and exit
```

`--doctor` runs offline checks (Python, imports, DeepSeek/WebSearch/sandbox/audit config, git,
workspace, trust, MCP, bundled skills) and prints actionable `PASS`/`WARN`/`FAIL` results without any
model/API calls or printing secrets. It exits `1` only if something **FAIL**s. `--doctor-live` adds a
small live WebSearch probe; `--json` emits machine-readable output.

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

In the REPL, slash commands include `/exit`, `/clear`, `/resume`, `/fork`, `/settings`, `/usage`,
and `/help`. `/usage` shows session token totals (input/output/cache) and an estimated cost; the
status bar shows a compact `133.4k in / 9.2k out | ~$0.42` summary once the model has been called.
Token counts fall back to local estimation when the provider omits usage fields, and costs use a
built-in DeepSeek pricing snapshot — treat them as estimates and override via `D2C_PRICING_*` when
pricing changes.

## How it works

Every entry surface — interactive REPL, headless one-shot, and MCP server — converges on a single async agent loop, `queryLoop()` in `src/d2c/loop.py`:

```
assemble context → call model → permission-gate tools → dispatch → shape context → repeat
```

The loop runs until the model returns a text-only response, hits the turn limit, or is stopped. Read-only tools run concurrently; writes and shell commands serialize.

Key subsystems (see [`CLAUDE.md`](./CLAUDE.md) for the full map):

- **Tools** (`tools/`) — 28 built-ins assembled through a single deny-first pool:
  - *files/search*: Read, Write, Edit, Glob, Grep, NotebookEdit, ListDir, FileInfo, ReplaceMany, JsonEdit, ApplyPatch
  - *shell/git/diagnostics*: Bash, GitStatus, GitDiff, EnvInfo, ConfigInfo, PackageInfo, CodeSymbols
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

## Security

See [`docs/security.md`](./docs/security.md) for the safety model, known protections, and limitations
(the sandbox is process-level, not a filesystem jail). The invariants are enforced by
`tests/test_security_regressions.py`.

## Development

The project was built as a numbered sequence of phases. Each phase has a design doc under [`plans/`](./plans/) and a matching commit. `plans/master_plan.md` is the original blueprint — treat it as historical intent; the code has since diverged.

```bash
pytest                                  # run all tests
pytest tests/test_loop.py               # a single file
pytest tests/test_loop.py::test_name    # a single test
```

Tests are async and use explicit `@pytest.mark.asyncio` markers — mark new async tests accordingly.

### Quality gates

CI (`.github/workflows/ci.yml`, Python 3.11 & 3.13) runs the same checks you can run locally:

```bash
ruff check .            # lint
ruff format --check .   # formatting
mypy                    # types (all of src/d2c; lenient baseline)
bandit -c pyproject.toml -r src/d2c   # security lint (justified skips in pyproject)
pip-audit               # dependency vulnerability scan (advisory)
pytest                  # tests
python -m build         # wheel/sdist build (includes bundled d2c/skills/*.md)
```

`ruff format .` and `ruff check --fix .` apply fixes. Typing is adopted in stages — `[tool.mypy].files`
lists the modules currently gated; expand it as modules are annotated. See `CONTRIBUTING.md`.

### Releases

The version lives in `d2c.__version__` (`src/d2c/__init__.py`); `pyproject.toml` reads it dynamically.
See [`CHANGELOG.md`](./CHANGELOG.md) and the release checklist in [`docs/release.md`](./docs/release.md).
A `v*` tag triggers `.github/workflows/release.yml` (gates → build → `twine check` → upload
artifacts). Publishing to PyPI is a deliberate, separate step.

See [`CLAUDE.md`](./CLAUDE.md) for architecture details and conventions.
