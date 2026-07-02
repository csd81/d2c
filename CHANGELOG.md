# Changelog

All notable changes to d2c are documented here. This project follows a simple
[Keep a Changelog](https://keepachangelog.com/) style.

## Unreleased

- **Phase 84:** DeepSeek-aware provider error messages. A new `provider_errors.py`
  (`classify_provider_error`/`format_provider_error` + `ProviderErrorInfo`) maps the documented
  HTTP failures — 400/401/402/403/404/422/429/500/503/504 — and connection/timeout errors into one
  concise, actionable message (e.g. `402` → insufficient balance, `422` → check model/thinking/
  max_tokens/tool params, `429`/`503` → retry shortly, `504` → smaller request), with a status
  code and a short **redacted** provider snippet for unknown codes. The loop's streaming and
  non-streaming paths share the formatter, and connection failures are kept distinct from HTTP
  status errors. Output never includes API keys, prompts, request bodies, or raw response objects.
- **Phase 83:** align DeepSeek model metadata with official serverless docs. First-class models
  now carry a **32K max output** (was 8192) and 128K context; the loop's output-token recovery
  caps at the resolved model's documented max (via `get_model_defaults`) instead of a stale global
  `32768`/`8192`. Pricing is now official rather than an estimate: `deepseek-v4-flash` is **free**
  ($0 input/cache/output) and `deepseek-v4-pro` is the **paid v4 tier** ($0.28/M in, $0.42/M out,
  $0.028/M cache-read), still overridable via `D2C_PRICING_*`. `--list-models` reports the 32K
  limit. Model-ID decision: `deepseek-v4-pro` stays the intentional canonical for `pro`/`v4`/`v4-pro`
  (its pricing set to the official paid v4 tier); raw/custom model IDs still pass through unchanged.
- **Phase 82:** opt-in DeepSeek thinking control. A `thinking` preset (`off` default / `low` /
  `medium` / `high`) maps to a reasoning token budget (4096 / 8192 / 16384) and, when not `off`,
  sends `extra_body={"thinking": {"type": "enabled", "budget_tokens": N}}` on the model call —
  the default request shape is otherwise unchanged. Set it with `--thinking` or `D2C_THINKING`
  (precedence `--thinking` > `D2C_THINKING` > default `off`; persist via `~/.d2c/.env`); invalid
  values fail (CLI) or warn (env). The active mode shows in `/settings` and the config/env info
  tools. All paths read it from the shared `Config` (the loop resolves it from `loop_config.config`),
  so REPL/Textual/headless/SDK/MCP/eval/server inherit one default. Hidden reasoning is not rendered
  into transcripts.
- **Phase 81:** the default model is now `deepseek-v4-flash` (faster/cheaper); `deepseek-v4-pro`
  stays available via `--model pro`. The first-class model surface is narrowed to Flash + Pro with
  aliases `flash`/`v4-flash` → flash and `pro`/`v4`/`v4-pro` → pro. The old `deepseek-chat` /
  `deepseek-reasoner` aliases (`chat`/`v3`/`reasoner`/`r1`) are removed from all help/`--list-models`
  output and docs; unknown/raw model IDs still pass through unchanged, so advanced users aren't
  broken. `--list-models` marks the default, and pricing defaults cover Flash (an estimate — the
  cheap tier — pending confirmed numbers; override via `D2C_PRICING_*`) and Pro. All paths (REPL,
  headless, SDK, eval, server) pick up the new default through shared `Config`.
- **Phase 80:** polish for the Textual-by-default transition. A persisted personal UI
  preference (`ui.default` in `~/.d2c/settings.yaml`) sits between `D2C_TUI` and the project
  default — precedence is now `--tui` > `D2C_TUI` > user `ui.default` > default (`textual`). Set
  it from the REPL with `/settings ui classic|textual|auto` (`auto` clears the override; other
  settings keys are preserved, atomic write). The preference is read from the USER settings file
  only, so a project/managed file can't force a user's UI. The missing-`[tui]` fallback note is
  shortened and now lists how to install, use `--tui classic`, or persist classic. Non-interactive
  paths are unchanged (guarded by a test that `run_headless` references no UI-preference/Textual
  code). Tests use a temporary settings path (autouse fixture) so the real `~/.d2c` is never touched.
- **Phase 79:** Textual is now the **default** interactive UI (`DEFAULT_UI = "textual"`), after
  the Phase 78 dogfooding fixes cleared the readiness blockers. `python -m d2c` uses Textual when
  the optional `[tui]` extra is installed and falls back to the classic prompt_toolkit REPL with a
  clear note when it isn't. Classic stays first-class: `--tui classic` and `D2C_TUI=classic` force
  it (with no note), and selection precedence (`--tui` > `D2C_TUI` > default) and the fallback
  logic are unchanged from Phase 77. Non-interactive paths (headless, `--json`, SDK, MCP, eval,
  server) are untouched and never start Textual. `docs/textual-readiness.md` records the revised
  GO decision.
- **Phase 78:** clears two Textual default-readiness blockers found in live dogfooding. The
  approval modal now has **clickable** `Deny / Once / Session / Always` buttons (mouse), mapping
  to the exact same Phase 52/64/65 scopes as the keyboard shortcuts — which are unchanged, with
  deny still the default (Esc/Enter/unknown) and non-approval keys a safe no-op. Both mouse and
  keyboard funnel through the one `apply_choice` path. Adds a `Ctrl+S` **selection mode** that
  pauses Textual's mouse capture (best-effort, where the driver allows) so transcript text can be
  drag-selected/copied, with a status-bar hint to use `Shift+drag` on terminals that keep mouse
  reporting on. `docs/textual-readiness.md` records the finding and resolution. Textual stays
  opt-in; the prompt_toolkit REPL is unchanged.
- **Phase 77:** Textual default-readiness audit + a `--tui classic|textual|auto` selector.
  Precedence is `--tui` > `D2C_TUI` > project default (still `classic`); `--tui textual`
  falls back to classic with a clear message when the `[tui]` extra isn't installed, and an
  invalid value errors via argparse. The resolution/fallback logic (`resolve_ui`,
  `ui_decision`) is pure and unit-tested, and `run_headless` is guarded to contain no Textual
  code path (headless never starts Textual even with `D2C_TUI=textual`). The audit report
  `docs/textual-readiness.md` records the parity matrix, safety review, and non-interactive
  boundary — decision: **NO-GO / keep opt-in**, with real-terminal dogfooding the one blocking
  gap. No default flip; when ready it's a one-line `DEFAULT_UI` change.
- **Phase 76:** session ergonomics for the experimental Textual UI (`D2C_TUI=textual`), to
  make it comfortable for real use. The transcript follows new output only when you're already
  at the bottom (scrolling up no longer yanks the viewport), with `PageUp`/`PageDown`/`Home`/
  `End` navigation; `Up`/`Down` walk prompt history (a small unit-tested `InputHistory` helper,
  starting recall only on an empty line so typed text isn't clobbered); `Ctrl+L` clears the
  view only (never session/history state); `Ctrl+C` clears a non-empty input else exits
  (conservative — it never kills an in-flight tool). Transcript blocks are visually distinct by
  role (user / assistant Markdown / tool row / error), tool rows color by ok/error/denied
  status, and the status footer truncates instead of overlapping on small terminals. Textual
  stays opt-in; the prompt_toolkit REPL is unchanged.
- **Phase 75:** the experimental Textual UI (opt-in, `D2C_TUI=textual`) gains the two core
  live-workflow surfaces. A **permission approval modal** shows the tool, category, reason,
  a redacted input preview (Bash commands with a shell-classifier risk label; Edit/Write/
  ApplyPatch with a `+N / -M` diff summary and a short inline diff), and maps `[y]`/`[a]`/
  `[A]`/`[n]` to the exact Phase 52/64/65 scopes — deny is the default (Escape/Enter/unknown).
  The modal only *collects* a choice; the existing `ApprovalCache` applies it, so approval
  semantics aren't duplicated, redaction goes through `observability.redact`, and nothing is
  read from disk speculatively. Tool executions now render as **compact timeline rows**
  (name, target preview, ok/error/denied status, file-count/error detail) reusing the same
  preview/diff helpers. Textual stays opt-in; the prompt_toolkit REPL is unchanged.
- **Phase 74:** experimental Textual TUI (opt-in), the first stage of a staged migration
  off the organically-grown prompt_toolkit REPL. Set `D2C_TUI=textual` (with the new
  optional extra: `pip install "d2c[tui]"`) to launch a Textual app shell — transcript +
  status footer + input — that reuses the Phase 72 command registry, renders assistant
  Markdown via Rich, and preserves the exact Phase 52/64/65 approval scopes. The default
  interactive UI stays prompt_toolkit; if `D2C_TUI=textual` is set without Textual
  installed, it prints a note and falls back. All `textual` imports are lazy, so importing
  `d2c.tui` (and the default REPL, headless, SDK, MCP, eval paths) never pulls Textual in.
  New `d2c/tui/` package with Textual-free, unit-tested helpers (command reuse,
  approval-choice mapping, Markdown fallback, status line) behind an `InteractiveUI`
  boundary; the Textual app itself is validated where Textual is installed.
- **Phase 73:** the interactive REPL now renders a pragmatic Markdown subset in assistant
  responses — headings, bullet/numbered lists, fenced and inline code, links (as
  `text (url)`), and blockquotes — via a small dependency-free renderer
  (`d2c.markdown_render`) built on the `prompt_toolkit` primitives already in use. It is
  display-only (never executes HTML, fetches links, or reads files) and fails open to plain
  text on any parse error. Each assistant text segment is buffered and rendered once when it
  completes, which also removes the old double-print (streamed tokens followed by a reprinted
  final block). Headless/single-shot, SDK, MCP, and eval output paths are unchanged (still
  plain text).
- **Phase 72:** REPL command UX consolidation. Slash commands now come from one shared
  registry (`SlashCommandSpec`) that drives `/help`, autocomplete, and unknown-command
  handling so they can't drift apart. `/help` is grouped by workflow (Session / State /
  Safety / Help) with the usage column aligned to the widest entry; autocomplete now
  covers common subcommands (`/approvals clear-session|reset`, `/profiles show|doctor`)
  as well as every top-level command; and an unknown command suggests the nearest match
  (`difflib`, e.g. `/aprovals` → "Did you mean /approvals?") or points to `/help`. No
  command semantics changed and no new feature commands were added.
- **Phase 71:** subagent profile UX. A new `/profiles` REPL command makes the Phase 61
  YAML capability profiles observable: `/profiles` lists loaded profiles (model,
  permission mode, tool-boundary summary); `/profiles show <name>` prints a profile's
  effective boundaries (model, permission mode, worktree isolation, allowed/denied
  tools, max turns/background) with instructions summarized by length + first heading
  rather than dumped; `/profiles doctor` reports loaded/skipped counts and the reason
  each profile was skipped (invalid YAML/fields via the loader's existing diagnostics,
  or "workspace is untrusted" for project profiles). Read-only and trust-aware —
  untrusted workspaces load no project profiles, and no env vars or full instruction
  bodies are ever printed. Unknown subcommands print usage and mutate nothing.
- **Phase 70:** approval-management UX. A new `/approvals` REPL command reports the
  session-approval count, persistent-approval count, and storage path (counts and
  path only — never the stored SHA-256 hashes or any original tool input);
  `/approvals clear-session` drops in-memory (`[a]`) approvals while leaving
  persisted (`[A]`) ones on disk, and `/approvals reset` is the in-app "forget
  everything" that empties the runtime set and deletes `~/.d2c/approvals.json` —
  no more manual file deletion. `ApprovalCache` gains `path()`, `runtime_count()`,
  `session_count()`, `persistent_count()`, and `clear_session()` (session =
  in-memory approvals not on disk; persistent = what survives a restart). Status is
  the non-destructive default; destructive actions require an explicit subcommand.
- **Phase 69:** a two-tier local quality gate replacing the removed GitHub Actions
  `ci` workflow. `scripts/check_fast.sh` is the inner-loop check (ruff lint +
  format check, mypy, and targeted tests when you pass paths); `scripts/check_release.sh`
  is the full gate to run before push/release/phase completion — a superset that
  adds the whole test suite, bandit, advisory pip-audit, a clean `dist/` build, and
  twine check. Both use `python -m pytest` (not bare `pytest`) to keep import
  behavior stable, and the release gate clears `dist/` before building so `twine
  check` isn't fooled by stale artifacts. README and CONTRIBUTING updated to point
  at them.
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
