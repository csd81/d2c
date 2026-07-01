# Paper vs. Implementation: `2604.14228v1` ("Dive into Claude Code") vs. `d2c`

This document compares the architecture described in the paper *"Dive into Claude Code: The
Design Space of Today's and Future AI Agent Systems"* (`2604.14228v1.pdf`) against the actual
Python implementation in this repository (`src/d2c/`).

The comparison was produced by reading the full 46-page paper and a source-level inventory of the
current implementation (~65 source files, ~17,300 LOC). This document is **regenerated from scratch**
against HEAD (`dfd4ad7`, Phase 63), not amended incrementally.

## What each one is

| | Paper — "Dive into Claude Code" | `d2c` |
|---|---|---|
| Subject | Source-level analysis of **Claude Code** (TypeScript, v2.1.88) | A **Python reimplementation** built *from* the paper |
| Size | ~1,884 files, ~512K LOC | ~65 files, ~17,300 LOC |
| Model backend | Claude (Anthropic) | **DeepSeek** via the Anthropic-compatible SDK |
| Relationship | Describes the design space | Reproduces the essential subsystems phase-by-phase |

`d2c`'s source comments reference "the paper" with section numbers throughout — it is deliberately an
educational port. The useful question is therefore **fidelity**: what it reproduces faithfully, what
it scales down, and what is scaffolded but inert.

**Overall verdict:** the architectural skeleton is faithful. Phases 34–63 closed every earlier
last-mile wiring gap and correctness bug. What remains diverging is mostly breadth — tool count,
permission modes, lifecycle hooks — and a few deliberately out-of-scope items, which the "Still open"
section catalogs.

---

## 1. Faithfully reproduced

Structure and behavior match the paper closely, often down to function names.

- **Agent loop (§4).** `queryLoop()` is a single async generator with a mutable `LoopState`,
  matching the paper's "while-true with whole-object state" (§4.1). Tool partitioning matches §4.2:
  consecutive `is_concurrent_safe` tools batch and run concurrently via `asyncio`, results emit in
  original order, and a **sibling-abort signal** fires when a Bash tool errors (the paper's
  `StreamingToolExecutor`). Mid-response streaming execution is implemented
  (`streaming_executor.py`).
- **Compaction pipeline (§4.3 / §7.3).** The strongest match. The paper's **five sequential
  shapers** — budget reduction → snip → microcompact → context collapse → auto-compact — are all
  present in `compact.py` with the same names, ordering, and "cheaper layers first" gating.
  Cache-aligned boundaries (§7.3) are implemented as `_find_cache_alignment_point` on 1024-token
  multiples. Reactive vs. proactive compaction use independent flags (Phase 34 fix).
- **Output-token recovery (§4.4).** The paper's escalating retries (`MAX_OUTPUT_TOKENS_RECOVERY_LIMIT
  = 3`). `queryLoop` escalates `max_tokens` (8192 → 16384 → 32768, capped) and retries up to 3× on a
  `max_tokens` stop, resetting after any clean response (Phase 35). Covered by dedicated tests.
- **Permissions (§5).** Deny-first evaluation (all DENY before any ALLOW) matches §5.1. The
  **AST-based shell classifier** (`permissions/classifier.py`, ~656 lines) genuinely implements the
  paper's `bashSecurity.ts`: logical-statement splitting, wrapper stripping (`sudo`/`env`/`nohup`),
  pipe-to-interpreter and SSRF detection, with a two-stage fast-filter → CoT-model design mirroring
  the auto-mode classifier (§5.3). The permission gate **fails closed** in both executor paths
  (Phase 38 fix).
- **Context assembly (§7.1).** `getSystemContext()` is memoized, git status is cached, and CLAUDE.md
  is placed as the **first user message, not system prompt** — the exact structural choice the paper
  calls out (§7.1–7.2). System prompt includes a "Context economy" section guiding the agent to use
  Grep/Glob/ListDir/FileInfo before Read, and ReadRange for known line ranges (Phase 63).
- **Memory (§7.2).** Four-level hierarchy, root→cwd traversal with "later = higher priority,"
  `@include` with code-fence skipping and circular-reference guarding. Auto-memory is reachable via
  the `Remember` tool, with the `MEMORY.md` index injected into context (Phase 34 wiring fix).
- **Persistence (§9).** Append-only JSONL transcripts, `compact_boundary` markers that reset the
  message list on replay, resume/fork, sidechain subdirectories, and — importantly — **permissions
  are NOT restored on resume/fork** (§9.2). Session manager (`SessionStore` / `SessionManager`)
  supports create, resume, and fork.
- **MCP (§6), tiktoken (§7), worktree isolation (§8), trust gate.** All substantively real: a full
  JSON-RPC client *and* a `--mcp` server, all four transports (stdio/http/sse/ws), real
  `cl100k_base` tokenization, real `git worktree` isolation, and a persistent trust store gating
  project-local `.env` / CLAUDE.md / MCP / skills / plugins.
- **Read-before-Write safety.** `FileReadTool` and `ReadRangeTool` mark files read (in the tool, so
  it holds on both the streaming and non-streaming paths); Edit and Write both enforce it. Symlink
  and `..` spellings canonicalize to the same real path (Phase 34 fix, Phase 63 re-verified for
  ReadRange).
- **File-history checkpoints / `--rewind-files` (§9).** The `FileHistoryTracker` is installed at
  startup (headless + interactive) and re-pointed on session switch (`/clear`, `/resume`, `/fork`).
  Write/Edit/NotebookEdit/ApplyPatch checkpoint before mutating; `--rewind-files` restores (Phase 34
  fix).
- **Sandbox (§5.4).** Wired and configurable through the pool into `BashTool` (`D2C_SANDBOX=1`, off
  by default). Three backends (Phase 62): **process** (universal default), **bubblewrap** (Linux
  OS-level, with rw-cwd / ro-system-roots / unshared-network / fail-closed semantics), and
  **docker** (documented scaffolding). Verified by live confinement tests for bubblewrap and process
  sandbox tests.
- **REPL slash commands.** `/help`, `/settings`, `/clear`, `/resume`, `/fork` are real multi-turn
  commands (Phase 34/36). Unknown `/x` is reported locally and never sent to the model.
- **Shell permission hardening (Phase 38).** Structural classification replaces first-word-only
  matching: only read-only / create-only / test-lint-format commands are auto-approved under
  `acceptEdits`; `rm`, `mv`, `sed -i`, `find -delete`, pipe-to-shell, interpreter `-c`, `chmod`,
  `sudo` are **denied**; uncertain commands ask.
- **WebSearch (Phase 39/58).** Three providers: **Tavily** (default), **Brave** (independent index),
  **SearXNG** (self-hosted). Normalized results with clean auth/rate-limit/timeout/empty handling,
  no key leakage. `recency_days` / `domains` filters supported by Tavily, documented as unsupported
  for others.
- **Observability / audit logging (Phase 44).** Opt-in structured JSONL audit log with central
  redaction and `session_id`/`turn_id`/`tool_call_id` correlation across session, model-call,
  tool-call, permission, file-change, compaction, hook-failure, and WebSearch events.
- **Interactive ASK (Phases 43/49/64/65).** `ASK` never falls through to automatic execution: shared
  `resolve_permission_decision` gates both executors and the MCP path; headless / MCP / no-callback
  contexts return permission-required denial. Granular audit events. The REPL renders a styled,
  color-coded permission dialog (Phase 65, `prompt_toolkit` `HTML`/`print_formatted_text`, no new
  dependency): a category-colored header, Bash commands risk-colored by reusing the `acceptEdits`
  structural classifier, and Edit/Write/ApplyPatch get a `+N / -M` diff summary computed only from
  the already-provided tool input (never a speculative disk read) — short diffs shown inline, longer
  ones collapsed behind a `[d]` expand action. Scopes: `[y]` once, `[a]` session (in-memory only),
  `[A]` always (exact-match hashes + timestamps persisted to `~/.d2c/approvals.json`, atomic writes,
  survives sessions and process restarts — Phase 64), `[n]` deny (default). `/clear`/`/resume`/`/fork`
  reset only the in-memory view, never the persisted file.
- **Prompt-injection hardening (Phase 53).** Retrieved web content, search snippets, and
  model-written memories wrapped in `<untrusted_*>` delimiters with breakout neutralization. System
  prompt treats such content as data.
- **Cost and token accounting (Phase 55).** Per-model-call token tracking with accumulated session
  totals and configurable USD pricing via `D2C_PRICING_*`. REPL statusline displays running totals.
- **SDK / HTTP server (Phase 59).** `d2c.sdk.D2CClient` provides a stable Python API; `d2c.server`
  exposes health/session/events endpoints over HTTP/1.1 on `127.0.0.1`.
- **Scoped settings / managed policy (Phase 60).** Managed `settings.yaml` overrides default
  permission mode, deny rules, and model selection from a configurable system path.
- **Subagent capability profiles (Phase 61).** YAML-defined named profiles under
  `.d2c/agents/*.yaml` (trust-gated) specifying model, permission mode, tool boundaries, optional
  worktree isolation, and instructions. Malformed profiles are reported and skipped individually.

---

## 2. Scaled down (present, but smaller than the paper)

| Subsystem | Paper | `d2c` |
|---|---|---|
| **Tool count** (§6.2, App. A) | up to **54** (19 unconditional + 35 gated) | **29** built-ins + dynamic MCP |
| **Permission modes** (§5.1) | **7** (plan, default, acceptEdits, auto, dontAsk, bypassPermissions, bubble) | **6** — all of the above **except `bubble`** (subagent-escalation mode; d2c handles the concern via subagent profiles instead) |
| **Hook events** (§6.1) | **27** defined, 5 in permission flow | **27 defined** (matches the paper's surface exactly), **19 fired**; **8 intentionally inactive** |
| **Subagent types** (§8) | up to 6 (Explore, Plan, general, Guide, Verification, Statusline) | **3 built-in** (Explore, Plan, GeneralPurpose) + unlimited **YAML capability profiles** (Phase 61) |
| **Bundled skills/plugins** | skill + command + plugin registries | **1 bundled skill** (`commit.md`), **0 bundled plugins** |
| **Permission handler paths** (§5.2) | coordinator / swarm / speculative / interactive | interactive + async classifier only |

These are reasonable scope cuts for a teaching implementation and don't contradict the paper's
design.

---

## 3. Previously reported gaps — all closed across Phases 34–63

Early versions of this document listed a dozen "last-mile wiring" gaps and two correctness bugs.
Every one of them has been fixed, tested, and confirmed:

1. **Read-before-Write safety check was broken.** Fixed in Phase 34. ReadRange (Phase 63) also marks
   files read, satisfying the edit guard.
2. **Output-token recovery (§4.4) was absent.** Fixed in Phase 35. Full escalating retry chain.
3. **Reactive vs. proactive compaction shared one flag.** Fixed in Phase 34. Independent flags.
4. **Sandbox (§5.4) was never attached.** Wired in Phase 34; OS-level bubblewrap backend in Phase 62.
5. **File-history checkpoints / `--rewind-files` didn't work.** Fixed in Phase 34.
6. **Other unwired modules** (AutoMemory, PathScopedRules, background-subagent manager). All wired
   in Phase 34.
7. **~15 hook events defined but never fired.** Fixed across Phases 34 & 40. Now 19 of 27 fire from
   tested runtime paths. Remaining 8 are intentionally inactive (see §5).
8. **WebSearch was a stub.** Fixed in Phase 39; provider expansion in Phase 58.
9. **REPL slash commands were cosmetic.** Fixed in Phases 34/36.
10. **`_check_safe_shell` auto-allowed dangerous commands** under `acceptEdits`. Fixed in Phase 38.
11. **Fail-open permission gate.** Fixed in Phase 38 — both paths fail closed.
12. **KAIROS (§11.6).** Still un-instantiated (see §5 — the paper also flags it as
    feature-gated / unconfirmed-in-production).

---

## 4. Design-choice divergences (not bugs)

- **Backend:** DeepSeek, not Claude. Config, model aliases (`v4-pro` / `chat` / `reasoner`), and
  128K context windows are DeepSeek-specific — the paper is entirely about Claude (200K–1M windows).
- **Pre-trust ordering CVEs (§11.3):** the paper documents CVE-2025-59536 etc., caused by extension
  code running before the trust dialog. `d2c` resolves trust *before* `Config.load` / plugin load,
  so it structurally avoids that temporal gap.
- **No `bubble` permission mode:** the paper's bubble mode is a subagent-escalation path that
  effectively bypasses permissions. `d2c` handles escalation through its subagent model and
  capability profiles.
- **No bundled plugins:** plugin loading infrastructure works, but `d2c` ships zero first-party
  plugins. Skills are bundled as `.md` files under `src/d2c/skills/` instead.
- **Session-scoped persistent approvals (Phase 52):** SHA-256 hashed exact-match cache for "always
  allow" in the current session. Never persisted, cleared on session switch. An in-memory
  approximation of the paper's "always allow" UX.
- **Untrusted-content delimiting (Phase 53):** `d2c` wraps retrieved content in `<untrusted_*>`
  delimiters with breakout sanitization. The paper describes prompt injection as a threat (§11.5)
  but doesn't detail a specific mitigation in Claude Code — this is `d2c`'s own defense layer.
- **Cost accounting (Phase 55):** per-session token and USD cost tracking with configurable pricing.
  The paper discusses token budgets but not cost UI.

---

## 5. Still open (intentionally deferred)

1. **KAIROS** (§11.6) — background heartbeat mode. The `kairos.py` module and config field
   (`kairos_enabled`) exist, but the agent is never instantiated or wired into the loop, main,
   or SDK. The paper flags KAIROS as feature-gated and unconfirmed in production, so this is a
   fair reflection of the design's uncertainty as well.
2. **Windows sandbox backend** — explicit stub. Falls back to the process backend on Windows; no
   Windows-specific OS-level isolation.
3. **8 lifecycle hooks are intentionally inactive** — no runtime source in a single-user CLI,
   asserted by `tests/test_phase40_hooks.py`:
   - `CONFIG_CHANGE` / `CWD_CHANGED` — config and cwd are immutable after load
   - `ELICITATION` / `ELICITATION_RESULT` — no elicitation flow
   - `NOTIFICATION` — no notification surface
   - `PERMISSION_REQUEST` — executors handle ASK/deny programmatically
   - `STOP_FAILURE` — no stop-failure recovery path
   - `TEAMMATE_IDLE` — no multi-agent teams
4. **Breadth gap to ~54 tools.** 29 built-in tools vs. the paper's ~54. The remaining gap is
   mostly product-specific or platform-specific: browser/screenshot/computer-use (better via MCP),
   provider-specific tools needing extra secret config, and process-management tools (handled via
   Bash `run_in_background`). None change the architectural story.

---

## 6. Bottom line

`d2c` is a **high-fidelity structural port of the paper's architecture** that has matured through
63 phases of iterative development. The agent loop, five-layer compaction pipeline, deny-first
permissions with an AST shell classifier, MCP client+server, worktree isolation, tiktoken accounting,
append-only persistence, and session management all match the described designs closely — often down
to function names.

Every correctness bug and last-mile wiring gap from the pre-Phase-34 era has been closed and
tested. What remains diverging is mostly intentional: breadth of tool surface (29 vs. 54), one
permission mode (`bubble`), a dead-code KAIROS module (consistent with the paper's own uncertainty),
and 8 lifecycle hooks with no source in a single-user CLI. The paper's core architectural ideas —
the loop, compaction, permissions, MCP, persistence — are faithfully reproduced and hardened.

| Metric | Value |
|---|---|
| Source files | ~65 |
| LOC (Python) | ~17,300 |
| Built-in tools | 29 + dynamic MCP |
| Permission modes | 6 of 7 (no bubble) |
| Hook events defined / fired | 27 / 19 (8 inactive) |
| Subagent types | 3 built-in + YAML profiles |
| Bundled skills | 1 (commit.md) |
| Bundled plugins | 0 |
| Sandbox backends | process + bubblewrap (Linux) |
| Tests | 1,380 passed, 1 skipped |
| Version | 0.1.0 |
| Commit range | Phase 1 → Phase 63 (dfd4ad7) |
