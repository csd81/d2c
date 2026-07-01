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

Several features exist as complete-looking modules but are **never wired into the loop**, so at
runtime they do nothing. Two are outright correctness bugs.

1. **Read-before-Write safety check is broken.** The paper requires Read-before-Edit/Write. In
   `d2c`, `FileReadTool` **never marks a file as read** — only `FileWriteTool` does. The safety gate
   can therefore essentially only be satisfied by a prior *write*, not a read. This is a bug, not a
   simplification.
2. **Output-token recovery (§4.4) is absent.** The paper describes escalating retries
   (`MAX_OUTPUT_TOKENS_RECOVERY_LIMIT = 3`). `d2c` has the `output_tokens_recovery_attempts` field
   but never reads it; `max_tokens` is hardcoded to 8192.
3. **Reactive vs. proactive compaction share one flag.** The `prompt_too_long` recovery (§4.4) and
   proactive `autoCompact` both consume `has_attempted_reactive_compact`, so only one can ever fire
   per session. The reactive path also does crude slice truncation rather than a real summary.
4. **Sandbox (§5.4) is implemented but never attached.** `BashTool` is constructed without a
   `sandbox_config`, so `should_use_sandbox()` is dead in the default pool. The Windows-sandbox
   backend is an explicit stub.
5. **File-history checkpoints / `--rewind-files` (§9) don't work.** `set_file_history_tracker()` is
   never called → the tracker is always `None` → no checkpoints are written → `--rewind-files` scans
   an empty directory. The feature is a no-op.
6. **KAIROS (§11.6) is completely un-instantiated** — dead code (the paper also flags it as
   feature-gated / unconfirmed-in-production, so this is a fair reflection).
7. **Other implemented-but-unwired modules:** `AutoMemoryStore` (auto-memory, §7.2),
   `PathScopedRules` (§7.1 path-scoped rules), background-subagent status tools (the manager exists
   but no tool exposes it to the model — yet the AgentTool prompt tells the model to use them), and
   `applyFullContextShapers`.
8. **~15 hook events defined but never fired**, including `SESSION_START`, `USER_PROMPT_SUBMIT`,
   `SUBAGENT_STOP`, and the `TASK_CREATED/COMPLETED` set.
9. **WebSearch is a stub** (returns "not configured").
10. **REPL slash commands are cosmetic** — `/clear`, `/resume`, `/fork`, `/settings`, `/help` are
    advertised by the completer but only `/exit`/`/quit` are handled.
11. **`_check_safe_shell` auto-allows `rm`, `mv`, `sed`** under `acceptEdits` via first-word-only
    matching. The paper lists these among acceptEdits auto-approvals, but combined with first-word
    parsing it's weaker than the paper's structural analysis implies.

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

Where it diverges, it's mostly **breadth** (15 tools vs 54, and 12 of the 27 hook events
actually firing) and
**"last-mile wiring"**: a cluster of subsystems (sandbox, file-history/rewind, auto-memory, path
rules, KAIROS, background-status tools) are built but not connected — plus two genuine correctness
issues: the Read-before-Write gate and the shared compaction flag.

### Highest-impact fixes, if pursued

1. Read-before-Write gate — have `FileReadTool` mark files read (small, correctness).
2. Wire `SandboxConfig` into `BashTool` in the pool (small, safety).
3. Call `set_file_history_tracker()` at startup so `--rewind-files` works (small, feature).
4. Give reactive and proactive compaction independent flags (small, robustness).
