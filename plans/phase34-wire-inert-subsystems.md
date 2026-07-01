# Phase 34: Wire up the built-but-inert subsystems

## Context

A source-level audit (see `COMPARISON.md`) found a cluster of subsystems that are fully coded and
tested but never connected to the runtime path, plus two correctness bugs. This phase connects them
so behavior matches what the code (and the paper) intend.

**Scope:** Groups 1–3 below. KAIROS background mode and a real WebSearch backend are out of scope.

**Recurring design decision:** tool `execute()` is called as `tool.execute(**tu.input)` with no
context object, and runs via two paths (`loop._execute_one_tool` non-streaming and
`streaming_executor.py` streaming — streaming is the REPL default). Rather than thread a context
arg through both executors and every tool, follow the existing `get_file_history_tracker()`
global-accessor pattern (`tools/__init__.py:65-74`): module-level "active runtime" accessors set
once at startup and read by the tools that need them.

---

## Group 1 — Core bugs + wiring

### 1.1 Read-before-Write gate (bug)
`FileReadTool` never marks files read → Write/Edit's "must Read first" guard (`write_tool.py:73`)
is unsatisfiable via Read. **Fix:** in `read_tool.py::FileReadTool.execute`, after a successful
read, call `mark_file_read(str(path))` (import from `d2c.tools.write_tool` inside the method).

### 1.2 Split compaction flags (bug)
`has_attempted_reactive_compact` gates both proactive auto-compact (`loop.py:489/491`) and
reactive-on-error compaction (`loop.py:628/630`). **Fix:** add
`has_attempted_proactive_compact` to `LoopState`; use it at 489/491.

### 1.3 Output-token recovery
`output_tokens_recovery_attempts` (`loop.py:78`) unused; `max_tokens` hardcoded 8192 (539, 605).
**Fix:** `max_tokens = min(8192 * 2**attempts, 32768)` at both sites; detect `stop_reason ==
"max_tokens"`, retry up to 3× incrementing attempts, reset on clean response. Caveat: DeepSeek may
cap output, making escalation a no-op upstream.

### 1.4 File-history tracker → `--rewind-files`
`set_file_history_tracker()` never called → no checkpoints. **Fix:** in `run_headless` and
`run_interactive`, construct `SessionFileHistory(Path.home()/".d2c", session_id, cwd=config.cwd)`,
wrap in `FileHistoryTracker`, call `set_file_history_tracker(tracker)`.

### 1.5 `FileHistory` import collision (latent bug)
`main.py:39` shadows prompt_toolkit's `FileHistory`; line 507 calls the wrong class. **Fix:** alias
`from d2c.file_history import FileHistory as SessionFileHistory, FileHistoryTracker`; use
`SessionFileHistory` at rewind dispatch (681) and 1.4.

### 1.6 Sandbox config → BashTool
`BashTool(cwd=...)` built with no `sandbox_config` (`pool.py:91`). **Fix:** add `sandbox_config`
field to pool `Config`, pass to BashTool; add `sandbox_enabled` (env `D2C_SANDBOX`) to
`config.Config`; build `PoolConfig(..., sandbox_config=SandboxConfig(enabled=...))` in main.

---

## Group 2 — Hook events + REPL slash commands

### 2.1 Fire the 4 missing events
- SESSION_START — after `_setup_session` in both run modes.
- USER_PROMPT_SUBMIT — headless before `queryLoop`; REPL near prompt read. Honor
  additional_context / veto.
- SUBAGENT_STOP — `subagent.py` before success return (~260-271) and error path.
- TASK_CREATED / TASK_COMPLETED — via a global active-hooks accessor set in main; fire in
  TaskCreateTool / TaskUpdateTool (on status→completed).

### 2.2 REPL slash commands
**Prerequisite:** REPL is stateless per-turn (`main.py:556-561`). Add a running `conversation`
list fed to `assembleMessages` and appended with assistant text. **Handlers** after `main.py:534`:
`/help`, `/settings`, `/clear` (new session), `/resume <id>`, `/fork <id>`.

---

## Group 3 — Memory + status tools

### 3.1 LazyMemoryLoader + PathScopedRules
`evaluate()` does NOT consult `_path_rules` (only `self.rules`) — must inject via `add_rules()`.
**Wire:** construct `path_rules = PathScopedRules()`, `loader = LazyMemoryLoader(cwd, path_rules)`,
expose via global accessor; in path-bearing tools call `on_file_accessed` and append returned
memory to `ToolResult.output`; in the executors call
`permission_engine.add_rules(path_rules.get_rules_for_path(path))`. Fix misleading docstring.

### 3.2 Background-subagent status tool
Add `BackgroundStatusTool` (name `AgentStatus`, READ) in `tools/background_status.py`; register in
`getAllBaseTools`; add `statuses()` to `BackgroundSubagentManager`; update `AgentTool` message.

### 3.3 Auto-memory (model-writable)
Add `MemoryTool` (name `Remember`, META) in `tools/memory_tool.py` wrapping `AutoMemoryStore`;
register; inject `MEMORY.md` index into `context.getUserContext`.

---

## Verification
`pytest`; manual Read→Write; rewind end-to-end; hooks fire; REPL slash commands + multi-turn;
`D2C_SANDBOX=1`. See `COMPARISON.md` for the audit this addresses.

## Out of scope
KAIROS background mode, WebSearch backend, smart auto-memory recall.
