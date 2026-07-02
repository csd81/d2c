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
export D2C_SANDBOX_BACKEND=bubblewrap                         # process (default) | bubblewrap | docker
export D2C_SANDBOX_NETWORK=0                                  # allow network inside the sandbox (off)
export D2C_SANDBOX_FALLBACK=0                                 # if the OS backend is missing: 0=fail closed, 1=fall back to process

# optional — enable the WebSearch tool. Provider is tavily (default), brave, or searxng:
export D2C_WEBSEARCH_PROVIDER=tavily
export D2C_WEBSEARCH_API_KEY=tvly-...
export D2C_WEBSEARCH_TIMEOUT=15                               # seconds (default)
# export D2C_WEBSEARCH_PROVIDER=brave
# export D2C_WEBSEARCH_API_KEY=BSA...
# export D2C_WEBSEARCH_PROVIDER=searxng                       # no API key needed
# export D2C_WEBSEARCH_BASE_URL=http://localhost:8080          # your SearXNG instance

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

# optional — override the managed settings.yaml location (see "Scoped settings" below):
export D2C_MANAGED_SETTINGS_PATH=/etc/d2c/settings.yaml       # default
```

**Audit logging** (opt-in) writes one redacted JSON object per line correlated by
`session_id` / `turn_id` / `tool_call_id` — session, model-call, tool-call, permission, file-change,
compaction, hook-failure, and WebSearch events. Secrets (API keys, `Authorization`, `sk-…`/`tvly-…`
shapes) are redacted; full prompts and tool outputs are **not** logged unless you opt in.

Without a configured provider, the `WebSearch` tool returns a clear "not configured" error instead
of results (searxng needs `D2C_WEBSEARCH_BASE_URL`; tavily/brave need `D2C_WEBSEARCH_API_KEY`).

WebSearch providers, roughly in order of setup effort:

| Provider  | Setup                                                  | Tradeoff |
|-----------|---------------------------------------------------------|----------|
| `tavily`  | API key, free tier with no credit card                  | Easiest agent-oriented hosted provider; default. |
| `brave`   | API key                                                  | Hosted provider with an independent index. |
| `searxng` | Point at a running instance (no account)                 | Self-hosted/no-vendor; reliability depends on the instance, and it must have JSON output enabled in `settings.yml`. |

Not every provider supports every `WebSearch` filter — `recency_days`/`domains` are Tavily-only
today; brave/searxng ignore them and the tool result notes which filters were dropped rather than
silently misapplying them.

Run `python -m d2c --doctor` to see which provider is configured and whether it's ready; add
`--doctor-live` for a real connectivity probe (never prints the key):

```bash
D2C_WEBSEARCH_PROVIDER=brave D2C_WEBSEARCH_API_KEY=... python -m d2c --doctor --doctor-live
D2C_WEBSEARCH_PROVIDER=searxng D2C_WEBSEARCH_BASE_URL=http://localhost:8080 python -m d2c --doctor --doctor-live
```

`.env` resolution: `~/.d2c/.env` always loads; a project-local `.env` loads **only if the workspace is trusted** (see [Workspace trust](#workspace-trust)). Shell environment variables take precedence over `.env` values.

Default model is `deepseek-v4-pro`. Aliases are accepted: `v3`/`chat` → `deepseek-chat`, `r1`/`reasoner` → `deepseek-reasoner`.

## Usage

```bash
python -m d2c                    # interactive REPL (rich prompt_toolkit console)
python -m d2c "fix the bug in foo.py"   # single-shot headless run
python -m d2c --mcp              # run as an MCP server (stdio JSON-RPC) for IDE integration
python -m d2c --serve            # run a local HTTP server (health + session endpoints)
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
| `--serve` | Start the local HTTP server |
| `--host` / `--port` | With `--serve`: bind address (default `127.0.0.1:8765`, localhost-only) |

In the REPL, slash commands include `/exit`, `/clear`, `/resume`, `/fork`, `/settings`, `/usage`,
`/approvals`, `/profiles`, and `/help`. `/approvals` reports the session and persistent approval
counts and the storage path (counts/path only — never stored hashes or tool inputs);
`/approvals clear-session` drops in-memory approvals and `/approvals reset` deletes the persisted
`~/.d2c/approvals.json`. `/profiles` lists the trusted subagent capability profiles (Phase 61);
`/profiles show <name>` prints a profile's effective model/mode/tool boundaries (instructions
summarized by length, never dumped) and `/profiles doctor` explains skipped or invalid profiles.
`/help` groups these by workflow (Session / State / Safety / Help), autocomplete covers commands
and common subcommands, and a mistyped command suggests the nearest match.
Assistant responses in the REPL are rendered as Markdown (headings, lists, fenced/inline code,
links, blockquotes) via a small dependency-free renderer that fails open to plain text; headless,
SDK, MCP, and eval output stay plain.

An **experimental Textual UI** is available behind an opt-in: install the extra
(`pip install "d2c[tui]"`) and run with `D2C_TUI=textual python -m d2c`. It reuses the same
slash commands, Markdown rendering (via Rich), and approval scopes as the default REPL, and adds
a permission approval modal (`[y]`/`[a]`/`[A]`/`[n]`, deny by default, with redacted input/diff
previews) and compact tool-progress timeline rows. The default interactive UI remains
prompt_toolkit; without the flag (or without Textual installed) nothing changes.
`/usage` shows session token totals (input/output/cache) and an estimated cost; the
status bar shows a compact `133.4k in / 9.2k out | ~$0.42` summary once the model has been called.
Token counts fall back to local estimation when the provider omits usage fields, and costs use a
built-in DeepSeek pricing snapshot — treat them as estimates and override via `D2C_PRICING_*` when
pricing changes.

## Programmatic use (SDK / local server)

`d2c.sdk.D2CClient` is a small, stable Python wrapper around the agent loop for scripts, IDE
integrations, and automation — no CLI/REPL required:

```python
from d2c.sdk import D2CClient

client = D2CClient(cwd=".")
async for event in client.run("summarize this repo"):
    ...  # TextDelta | TextResponse | ToolExecutionEvent | StopEvent — same events the CLI consumes
```

Each `run()` call is one turn against a persistent on-disk session (the same
`d2c.persistence.SessionStore` mechanism the CLI uses); a session is created on first use and reused
by `client.session_id` on subsequent calls, or resume an existing one with `run(prompt,
session_id=...)`. Like headless CLI mode, `D2CClient` has no interactive approval prompt, so `ASK`
permission decisions fail closed under the default permission mode — pass `permission_mode="bypass"`
(or `"acceptEdits"`/`"dontAsk"`) for unattended automation.

`python -m d2c --serve` runs a minimal local HTTP server (hand-rolled over `asyncio`, no new
dependency) exposing the same functionality over JSON, **localhost-only by default**:

| Method & path | Description |
|---|---|
| `GET /health` | `{"status": "ok", "version": "..."}` |
| `POST /sessions` | Create a session; optional `{"model": "..."}` body. Returns `{"session_id"}`. |
| `POST /sessions/{id}/messages` | Run one turn; `{"prompt": "..."}` body. Blocks until the turn completes; returns `{"session_id", "text", "stop_reason"}`. |
| `GET /sessions/{id}/events` | All recorded loop events for that session (tool inputs redacted). |

This is groundwork for a local daemon, not a production server: no auth, no TLS, one request per
connection. Only bind beyond `127.0.0.1` if you understand the exposure.

## How it works

Every entry surface — interactive REPL, headless one-shot, and MCP server — converges on a single async agent loop, `queryLoop()` in `src/d2c/loop.py`:

```
assemble context → call model → permission-gate tools → dispatch → shape context → repeat
```

The loop runs until the model returns a text-only response, hits the turn limit, or is stopped. Read-only tools run concurrently; writes and shell commands serialize.

Key subsystems (see [`CLAUDE.md`](./CLAUDE.md) for the full map):

- **Tools** (`tools/`) — 29 built-ins assembled through a single deny-first pool:
  - *files/search*: Read, ReadRange, Write, Edit, Glob, Grep, NotebookEdit, ListDir, FileInfo, ReplaceMany, JsonEdit, ApplyPatch
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

## Scoped settings

`Config.load()` layers governance settings from four YAML scopes, same precedence pattern as the
CLAUDE.md hierarchy:

| Scope | Location | Trust-gated? |
|---|---|---|
| managed | `/etc/d2c/settings.yaml` (or `D2C_MANAGED_SETTINGS_PATH`) | no — always loaded |
| user | `~/.d2c/settings.yaml` | no — always loaded |
| project | `.d2c/settings.yaml` | yes — untrusted workspaces skip it |
| local | `.d2c/settings.local.yaml` (gitignore this) | yes — untrusted workspaces skip it |

Supported keys: `permission_mode`, `sandbox_enabled` (scalars — the highest scope that sets one
wins outright; a value set by `managed` **cannot** be overridden by `user`/`project`/`local`, only
recorded as a blocked override attempt), and `permission_rules` / `hooks` (lists — unioned across
every scope, so a `managed` deny rule always applies regardless of what a lower scope allows: the
permission engine checks all deny rules before any allow rule). A malformed settings file, an
invalid `permission_mode`, or an invalid rule/hook entry is reported as a warning (surfaced via
`config.validate()` and `python -m d2c --doctor`) and skipped — it never crashes the session. Don't
put secrets in settings YAML; keep those in `.env`/environment variables.

```yaml
# /etc/d2c/settings.yaml — example managed lockdown
permission_mode: default
permission_rules:
  - type: deny
    pattern: Bash
    reason: "shell disabled by org policy"
```

## Subagent profiles

The built-in subagent types (`Explore`, `Plan`, `General-purpose`) can be extended with named
**capability profiles** — YAML files in a trusted workspace's `.d2c/agents/*.yaml` (or `.yml`). A
profile sets the subagent's model, permission mode, tool allow/deny boundaries, optional git-worktree
isolation, and instructions (system prompt):

```yaml
# .d2c/agents/security-reviewer.yaml
name: security-reviewer
model: deepseek-reasoner
permission_mode: plan
tools:
  allow: [Read, Grep, Glob, GitDiff]
  deny: [Write, Edit, Bash]
isolation: worktree           # run in a throwaway git worktree
instructions: |
  Review the changes for security vulnerabilities. Do not modify files.
```

The model invokes it by name via the `Agent` tool (`subagent_type: "security-reviewer"`), and can
request `isolation: worktree` per call to override a profile's setting. Profiles are project-local,
executable-ish config, so — like `.env`, CLAUDE.md, skills, and MCP — they load **only in a trusted
workspace**; an untrusted workspace sees only the built-in types. A malformed profile (bad YAML,
missing `name`, invalid `permission_mode`/`isolation`, wrong tool shape) is reported and skipped, not
loaded. `tools.allow` narrows the pool to exactly those tools; `tools.deny` (used when no `allow` is
given) removes them.

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

There is no automatic GitHub CI. The gate runs locally in two tiers:

```bash
./scripts/check_fast.sh [tests/...]   # inner loop: lint + format + types (+ targeted tests)
./scripts/check_release.sh            # before push / release / phase: the full suite
```

`check_fast.sh` is the quick pre-commit check (ruff, format, mypy, and targeted
tests if you pass paths). `check_release.sh` is the comprehensive gate — a
superset that adds the full test suite, bandit, advisory pip-audit, a clean
`dist/` build, and twine check. Both use `python -m pytest` (stable import
behavior). The individual steps, if you want to run them one at a time:

```bash
python -m ruff check .            # lint
python -m ruff format --check .   # formatting
python -m mypy                    # types (all of src/d2c; lenient baseline)
python -m bandit -c pyproject.toml -r src/d2c   # security lint (justified skips in pyproject)
python -m pip_audit               # dependency vulnerability scan (advisory)
python -m pytest                  # tests
python -m build                   # wheel/sdist build (includes bundled d2c/skills/*.md)
python -m twine check dist/*      # artifact validation
```

`ruff format .` and `ruff check --fix .` apply fixes. Typing is adopted in stages — `[tool.mypy].files`
lists the modules currently gated; expand it as modules are annotated. See `CONTRIBUTING.md`.

### Releases

The version lives in `d2c.__version__` (`src/d2c/__init__.py`); `pyproject.toml` reads it dynamically.
See [`CHANGELOG.md`](./CHANGELOG.md) and the release checklist in [`docs/release.md`](./docs/release.md).
A `v*` tag triggers `.github/workflows/release.yml` (gates → build → `twine check` → upload
artifacts). Publishing to PyPI is a deliberate, separate step.

See [`CLAUDE.md`](./CLAUDE.md) for architecture details and conventions.
