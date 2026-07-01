# Phase 37: Stabilization and regression audit

**Priority:** HIGHEST (Correctness after core runtime wiring)

## Context

Phases 34-36 changed core runtime behavior:

- Read/Edit/Write safety
- file-history checkpoints and `--rewind-files`
- sandbox wiring
- output-token recovery
- REPL multi-turn state
- real slash commands
- path-scoped permission rules
- hook firing
- background status and memory tools

Before adding another subsystem, the highest-ROI work is to prove these changes are stable and fix
the regressions they expose.

## Goal

Run a focused stabilization pass:

1. Run the full test suite.
2. Fix failures caused by recent integration work.
3. Add missing regression tests around high-risk runtime paths.
4. Manually smoke-test interactive flows.
5. Update docs/audit notes only where behavior has been verified.

## Scope

In scope:

- bug fixes
- small refactors that make tests or runtime behavior clearer
- missing tests for recently wired systems
- documentation corrections tied to verified behavior

Out of scope:

- new WebSearch backend
- new built-in tools
- production plugin ecosystem
- new permission mode such as `bubble`
- large REPL/TUI redesign

## Work Plan

### 1. Full Test Run

Run:

```bash
pytest
```

If failures appear, classify them:

- test expectation stale after intended behavior change
- real runtime regression
- test isolation issue
- network/environment dependency

Fix real regressions first. Only update test expectations when the new behavior is intentional and
documented.

### 2. REPL Slash Command Smoke Test

Manually run:

```bash
python -m d2c
```

Verify:

```text
/help
/settings
normal user prompt
/clear
normal user prompt after clear
/resume <known-session-id>
/fork <known-session-id>
/quit
```

Expected results:

- unknown slash commands are not sent to the model
- `/settings` prints no secrets
- `/clear` creates a fresh active session
- `/resume` replaces active messages from transcript
- `/fork` creates a new session from source transcript
- later prompts use the new active session

### 3. Read/Edit/Write Safety Audit

Verify the highest-risk file safety invariant:

- `Edit` without prior `Read` is blocked
- `Write` without prior `Read` is blocked when overwriting existing files
- `Read` then `Edit` succeeds
- path normalization is consistent between read tracking and write/edit checks
- streaming and non-streaming tool execution paths behave the same

Add or strengthen tests if any of these cases are missing.

### 4. File-history / Rewind Audit

Verify:

- file-history tracker is installed in headless mode
- file-history tracker is installed in interactive mode
- Write/Edit create checkpoints before mutation
- `--rewind-files <session_id>` restores changed files
- session switching through `/clear`, `/resume`, and `/fork` points the tracker at the correct
  active session

### 5. Sandbox Wiring Audit

Verify:

- default behavior is unchanged when sandbox is disabled
- `D2C_SANDBOX=1` passes a `SandboxConfig` to `BashTool`
- sandboxed execution path is reachable
- dangerous commands still pass through permission checks
- Windows sandbox limitations are explicit in docs/comments if still incomplete

### 6. Output-token Recovery Audit

Verify:

- retry budget escalates as expected
- retry limit is enforced
- clean responses reset the counter
- responses with tool calls do not retry
- streaming and non-streaming paths are covered

This should mostly be satisfied by Phase 35 tests; only add tests if gaps remain.

### 7. Hooks and Path Rules Audit

Verify:

- newly wired hook events fire once, in the expected order
- hook failures do not crash unrelated flows unless designed to veto
- path-scoped rules are consulted during permission evaluation
- path-scoped rules do not accumulate duplicate global rules indefinitely

### 8. Docs and Audit Update

After tests/manual checks pass, update:

- `COMPARISON.md` resolved/unresolved notes
- `README.md` command behavior if needed
- `CLAUDE.md` architecture notes if implementation details changed

Do not mark a feature as resolved just because code exists. Mark it resolved only after behavior is
tested or manually verified.

## Tests to Run

Minimum:

```bash
pytest
```

Focused if debugging:

```bash
pytest tests/test_phase34.py
pytest tests/test_loop_output_recovery.py
pytest tests/test_repl_commands.py
pytest tests/test_tools.py
pytest tests/test_persistence.py
```

## Acceptance Criteria

- Full test suite passes, or remaining failures are documented as unrelated/environmental.
- REPL slash commands pass manual smoke testing.
- Read/Edit/Write safety has regression coverage.
- File-history rewind is verified end to end.
- Sandbox wiring is verified at least through construction and reachable execution path.
- `COMPARISON.md` no longer overstates gaps that are now tested and working.

## Expected Outcome

The project becomes deeper rather than broader:

- fewer inert subsystems
- fewer advertised-but-broken behaviors
- stronger safety invariants
- more trustworthy docs
- a stable base for the next feature phase
