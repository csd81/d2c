# Phase 68: Eval-guided tool tuning

**Priority:** HIGH (use Phase 67 data before adding more tools)

## Context

Phase 67 added a checked-in eval corpus and a measured baseline against live
DeepSeek. The baseline gives two actionable findings:

1. `add-test-coverage` is marked as the lone failure, but it is a false
   negative: the model added the requested test correctly, then verified with
   the full suite and hit the fixture's intentional `test_multiply` bug.
2. The cross-file rename task completed with four `Edit` calls instead of the
   expected `ApplyPatch`, giving a concrete tool-description/schema tuning
   target.

Phase 68 should make the smallest changes that improve the eval signal and then
measure whether targeted tool guidance changes behavior.

## Goal

Improve tool-selection behavior using the Phase 67 corpus as the measurement
loop, without guessing or adding new tools.

Primary questions:

1. Can the eval harness distinguish task success from unrelated verification
   failures?
2. Can `ApplyPatch` become the preferred tool for coordinated multi-file edits?
3. Do description/schema changes reduce tool-call count, turns, or advisory
   divergences without hurting success rate?

## Scope

In scope:

- success semantics for eval tasks where verification can expose unrelated
  fixture failures
- `ApplyPatch` description/schema guidance
- optionally small guidance updates for `ReplaceMany`, `ReadRange`, and
  inspection tools if Phase 67 data supports them
- re-running the same Phase 67 corpus
- documenting before/after results
- tests for any harness or schema-description changes

Out of scope:

- adding `MultiEdit` or other new tools
- broad prompt rewrites
- changing model provider
- changing corpus tasks to make the metrics look better
- pass/fail grading as a full eval framework
- parallel eval runner redesign beyond bug fixes

## Work Plan

### 1. Tighten eval success semantics

Inspect `src/d2c/eval.py` and the Phase 67 corpus expectations. Add the minimal
mechanism needed to avoid false failures from unrelated fixture checks.

Candidate design:

- Keep `success` as the harness-level execution outcome.
- Add an optional advisory field such as `expect.success_if_files_changed`,
  `expect.ignore_verification_failures`, or `expect.allowed_failure_patterns`.
- Prefer a narrow corpus-local solution over a general grading system.

Acceptance for this step:

- `add-test-coverage` can be documented as task-successful while still surfacing
  the unrelated full-suite failure as a divergence or note.
- CI-safe tests validate the new expectation parsing and summary behavior.

### 2. Tune `ApplyPatch` discoverability

Update `src/d2c/tools/apply_patch.py` tool description and/or schema text so the
model sees `ApplyPatch` as the right primitive for:

- coordinated edits across multiple files
- rename-like changes that touch several files
- compact patch-shaped changes where separate `Edit` calls would repeat context

Keep the guidance specific. Do not oversell `ApplyPatch` for single-location
edits where `Edit` is simpler.

Potential description shape:

```text
Use ApplyPatch for coordinated multi-file edits, renames, or changes that are
naturally represented as a unified diff. Prefer Edit for one exact replacement
in one file.
```

### 3. Consider narrow guidance for adjacent tools

Only if baseline data justifies it:

- `ReplaceMany`: clarify when multiple exact replacements in one file are better
  than repeated `Edit`.
- `ReadRange`: clarify that known line ranges should use it before full `Read`.
- `CodeSymbols` / `PackageInfo`: clarify read-only inspection before shelling
  out.

Do not change these without a concrete Phase 67 metric or task divergence.

### 4. Re-run the Phase 67 corpus

Run the exact same corpus against the tuned branch:

```bash
python -m d2c eval eval/corpus.yaml --out-dir eval-results-phase68
python -m json.tool eval-results-phase68/summary.json
```

Compare against the Phase 67 baseline at commit `41131a2`.

Key metrics:

- success count
- advisory divergence count
- `ApplyPatch` call count/share
- `Edit` call count/share
- mean and median turns
- total tool calls
- estimated cost
- repeated `Edit` sequences on the cross-file rename task

### 5. Document results

Add either:

- a new `eval/phase68-results.md`, or
- a clearly marked Phase 68 section in `eval/baseline.md`.

The report should include:

- old vs. new commit hashes
- command used
- before/after table
- whether the cross-file rename changed tool choice
- any regressions
- recommendation for the next phase

## Files to Inspect / Modify

Likely:

```text
src/d2c/eval.py
src/d2c/tools/apply_patch.py
eval/corpus.yaml
eval/baseline.md
eval/README.md
tests/test_eval.py
tests/test_eval_corpus.py
```

Optional:

```text
src/d2c/tools/structured_edit.py
src/d2c/tools/read_range_tool.py
src/d2c/context.py
eval/phase68-results.md
CHANGELOG.md
```

## Tests

Add or update tests for:

1. New eval expectation fields parse correctly.
2. False-negative handling preserves failure details as divergence/note.
3. `ApplyPatch` API format includes the tuned guidance.
4. Existing eval corpus hygiene still passes.

Run:

```bash
python -m pytest tests/test_eval.py tests/test_eval_corpus.py tests/test_phase51_tools.py
python -m ruff check .
python -m ruff format --check .
python -m mypy
```

Full gate before push:

```bash
python -m pytest
python -m bandit -c pyproject.toml -r src/d2c
python -m pip_audit
python -m build
python -m twine check dist/*
```

## Acceptance Criteria

- The Phase 67 false-negative failure is represented accurately without hiding
  real execution errors.
- `ApplyPatch` description/schema clearly targets coordinated multi-file edits.
- The same corpus is re-run and results are documented.
- The Phase 68 report compares against commit `41131a2`.
- Existing gates stay green.
- No fixture repos are mutated by the eval run.

## Expected Outcome

The project gets its first measured tool-selection improvement loop. If
`ApplyPatch` usage improves on the cross-file rename without regressions, keep
the tuned guidance. If not, the evidence justifies either stronger examples,
corpus adjustments, or a future `MultiEdit` design.
