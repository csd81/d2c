# Paper vs. Implementation: `2604.14228v1` ("Dive into Claude Code") vs. `d2c`

This document compares the architecture described in the paper *"Dive into Claude Code: The
Design Space of Today's and Future AI Agent Systems"* (`2604.14228v1.pdf`) against the actual
Python implementation in this repository (`src/d2c/`).

The comparison was produced by reading the full 46-page paper and a source-level inventory of the
implementation (~51 files, ~11,200 LOC).

## What each one is

| | Paper — "Dive into Claude Code" | `d2c` |
|---|---|---|
| Subject | Source-level analysis of **Claude Code** (TypeScript, v2.1.88) | A **Python reimplementation** built *from* the paper |
| Size | ~1,884 files, ~512K LOC | ~51 files, ~11,200 LOC |
| Model backend | Claude (Anthropic) | **DeepSeek** via the Anthropic-compatible SDK |
| Relationship | Describes the design space | Reproduces the essential subsystems phase-by-phase |

`d2c`'s source comments reference "the paper" with section numbers throughout — it is deliberately an
educational port. The useful question is therefore **fidelity**: what it reproduces faithfully, what
it scales down, and what is scaffolded but inert.

**Overall verdict:** the architectural skeleton is faithful; roughly a dozen features are present as
code but not wired into the running loop, plus two genuine correctness bugs.

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
  multiples.
- **Permissions (§5).** Deny-first evaluation (all DENY before any ALLOW) matches §5.1. The
  **AST-based shell classifier** (`permissions/classifier.py`, ~656 lines) genuinely implements the
  paper's `bashSecurity.ts`: logical-statement splitting, wrapper stripping (`sudo`/`env`/`nohup`),
  pipe-to-interpreter and SSRF detection, with a two-stage fast-filter → CoT-model design mirroring
  the auto-mode classifier (§5.3).
- **Context assembly (§7.1).** `getSystemContext()` is memoized, git status is cached, and CLAUDE.md
  is placed as the **first user message, not system prompt** — the exact structural choice the paper
  calls out (§7.1–7.2).
- **Memory (§7.2).** Four-level hierarchy, root→cwd traversal with "later = higher priority,"
  `@include` with code-fence skipping and circular-reference guarding.
- **Persistence (§9).** Append-only JSONL transcripts, `compact_boundary` markers that reset the
  message list on replay, resume/fork, sidechain subdirectories, and — importantly — **permissions
  are NOT restored on resume/fork** (§9.2).
- **MCP (§6), tiktoken (§7), worktree isolation (§8), trust gate.** All substantively real: a full
  JSON-RPC client *and* a `--mcp` server, all four transports (stdio/http/sse/ws), real
  `cl100k_base` tokenization, real `git worktree` isolation, and a persistent trust store gating
  project-local `.env` / CLAUDE.md / MCP / skills / plugins.

---

## 2. Scaled down (present, but smaller than the paper)

| Subsystem | Paper | `d2c` |
|---|---|---|
| **Tool count** (§6.2, App. A) | up to **54** (19 unconditional + 35 gated) | **15–16** built-ins + dynamic MCP |
| **Permission modes** (§5.1) | **7** (plan, default, acceptEdits, auto, dontAsk, bypassPermissions, bubble) | **6** — has AUTO + BYPASS, **no `bubble`** (subagent-escalation mode) |
| **Hook events** (§6.1) | **27** defined, 5 in permission flow | **27 defined** (matches), but only **12 actually fired** |
| **Subagent types** (§8) | up to 6 (Explore, Plan, general, Guide, Verification, Statusline) | 3 (Explore, Plan, general-purpose) |
| **Bundled skills/plugins** | skill + command + plugin registries | **1 skill** (`commit.md`), **0 bundled plugins** |
| **Permission handler paths** (§5.2) | coordinator / swarm / speculative / interactive | interactive + async classifier only |

These are reasonable scope cuts for a teaching implementation and don't contradict the paper's
design.

---

## 3. Gaps and bugs (scaffolded but inert, or behaviorally wrong)

> **Status update:** The gaps below describe the pre-Phase-34 state. **Phases 34–35 resolved most
> of them** (see `plans/phase34-wire-inert-subsystems.md` and `plans/phase35-output-token-recovery.md`).
> Resolved items are marked ✅ inline.

Several features exist as complete-looking modules but are **never wired into the loop**, so at
runtime they do nothing. Two are outright correctness bugs.

1. **Read-before-Write safety check is broken.** ✅ *Fixed in Phase 34; regression-tested in Phase 37.*
   `FileReadTool` now marks files read (in the tool, so it holds on both the streaming and
   non-streaming paths); Edit and Write both enforce it. Covered by `tests/test_phase34.py` and
   `tests/test_phase37.py` (Edit-without-Read blocked, Read-then-Edit succeeds).
2. **Output-token recovery (§4.4) is absent.** ✅ *Fixed in Phase 35.* The paper describes escalating
   retries (`MAX_OUTPUT_TOKENS_RECOVERY_LIMIT = 3`). `d2c` previously had the
   `output_tokens_recovery_attempts` field but never read it; `max_tokens` was hardcoded to 8192.
   Now `queryLoop` escalates `max_tokens` (8192 → 16384 → 32768, capped) and retries up to 3× on a
   `max_tokens` stop, resetting after any clean response; covered by `tests/test_loop_output_recovery.py`.
3. **Reactive vs. proactive compaction share one flag.** ✅ *Fixed in Phase 34.* Proactive
   `autoCompact` now uses `has_attempted_proactive_compact`, independent of the reactive
   `prompt_too_long` path. (The reactive path still does a crude slice truncation rather than a full
   summary — an intentional simplification, not a blocker.)
4. **Sandbox (§5.4) is implemented but never attached.** ✅ *Wired in Phase 34; tested in Phase 37.*
   `SandboxConfig` flows through the pool into `BashTool` (`D2C_SANDBOX=1`, off by default); the
   process backend is reachable and exercised on POSIX and Windows. Covered by `tests/test_sandbox.py`
   and `tests/test_phase37.py`. *The Windows-sandbox backend remains an explicit stub* (falls back to
   the process backend).
5. **File-history checkpoints / `--rewind-files` (§9) don't work.** ✅ *Fixed in Phase 34; tested in
   Phase 37.* The tracker is installed at startup (headless + interactive) and re-pointed on session
   switch (`/clear`, `/resume`, `/fork`). Write/Edit checkpoint before mutating; `--rewind-files`
   restores. Covered by `tests/test_phase34.py` (checkpoint + rewind end-to-end) and
   `tests/test_phase37.py` (tracker re-points on `/clear`).
6. **KAIROS (§11.6) is completely un-instantiated** — dead code (the paper also flags it as
   feature-gated / unconfirmed-in-production, so this is a fair reflection). *Still unresolved (out of
   scope).*
7. **Other implemented-but-unwired modules.** ✅ *Mostly wired in Phase 34.* `AutoMemoryStore` is now
   reachable via the `Remember` tool with the `MEMORY.md` index injected into context;
   `PathScopedRules` are consulted by `PermissionEngine.evaluate` (Phase 34/37); the
   background-subagent manager is exposed via the `AgentStatus` tool. (`applyFullContextShapers`
   remains an unused duplicate of the loop's inlined shaper pipeline — harmless dead code.)
8. **~15 hook events defined but never fired.** ✅ *Partially fixed in Phase 34.* `SESSION_START`,
   `USER_PROMPT_SUBMIT`, `SUBAGENT_STOP`, and `TASK_CREATED`/`TASK_COMPLETED` now fire; other
   lifecycle events (e.g. `CWD_CHANGED`, `FILE_CHANGED`, elicitation) remain unfired.
9. **WebSearch is a stub** (returns "not configured"). *Still unresolved (out of scope — needs a real
   search backend, not wiring).*
10. **REPL slash commands are cosmetic.** ✅ *Fixed in Phases 34/36.* `/help`, `/settings`, `/clear`,
    `/resume`, `/fork` are real (and the REPL is now multi-turn); unknown `/x` is reported locally and
    never sent to the model. Covered by `tests/test_repl_commands.py`.
11. **`_check_safe_shell` auto-allows `rm`, `mv`, `sed`** under `acceptEdits` via first-word-only
    matching. The paper lists these among acceptEdits auto-approvals, but combined with first-word
    parsing it's weaker than the paper's structural analysis implies. *Still unresolved (out of scope).*

---

## 4. Design-choice divergences (not bugs)

- **Backend:** DeepSeek, not Claude. Config, model aliases (`v4-pro` / `chat` / `reasoner`), and
  128K context windows are DeepSeek-specific — the paper is entirely about Claude (200K–1M windows).
- **Pre-trust ordering CVEs (§11.3):** the paper documents CVE-2025-59536 etc., caused by extension
  code running before the trust dialog. `d2c` resolves trust *before* `Config.load` / plugin load,
  so it structurally avoids that specific temporal gap.
- **Fail-open permission:** in `d2c`, if `evaluate_async` throws, the tool is treated as *allowed*.
  The paper's thesis is defense-in-depth / deny-first; fail-open is the opposite posture and worth
  flagging.

---

## 5. Bottom line

`d2c` is a **high-fidelity structural port of the paper's architecture**. The agent loop, the
five-layer compaction pipeline, deny-first permissions with an AST shell classifier, MCP
client+server, worktree isolation, tiktoken accounting, and append-only persistence all match the
described designs closely, often down to function names.

Most of the original "last-mile wiring" gaps and both correctness bugs were **closed in Phases
34–37** (Read-before-Write, file-history/rewind, sandbox, path rules, hook firing, auto-memory,
background-status, output-token recovery, compaction-flag split, real slash commands) — each now
covered by tests and the full suite is green. What remains diverging is mostly **breadth** (17 tools
vs 54; a subset of the 27 hook events firing) and a few **deliberately out-of-scope** items.

### Still open (intentionally deferred)

1. **KAIROS** background heartbeat mode — un-instantiated (paper flags it as unconfirmed too).
2. **WebSearch** — stub; needs a real search backend, not wiring.
3. **`_check_safe_shell`** first-word-only matching auto-allows `rm`/`mv`/`sed` under `acceptEdits`.
4. **Windows sandbox backend** — explicit stub (falls back to the process backend).
5. Remaining unfired lifecycle hooks (`CWD_CHANGED`, `FILE_CHANGED`, elicitation, …).
