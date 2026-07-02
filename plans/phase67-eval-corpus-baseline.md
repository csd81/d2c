# Phase 67: Eval corpus and baseline report

**Priority:** HIGH (make Phase 66's harness actionable)

## Context

Phase 66 added a headless eval harness that can run YAML task corpora through
`d2c.sdk.D2CClient` and report tool usage, turns, token/cost totals, compaction
events, tool sequences, outcomes, and advisory expectation divergences.

That harness is useful only after the repository has a small, repeatable corpus
and a baseline report. Without that baseline, follow-up work like tool
description tuning or adding more edit tools is guesswork.

## Goal

Create a checked-in eval corpus and baseline workflow that answers questions like:

1. Does the model use `ApplyPatch` for multi-file edits?
2. Does it use lightweight inspection tools before full `Read`?
3. Does `ReadRange` reduce context for targeted edits?
4. Which tasks burn turns, tokens, or repeated tool calls?
5. Which tool descriptions or schemas should Phase 68 tune first?

## Scope

In scope:

- a small checked-in YAML corpus
- tiny local fixture repos/tasks
- documented commands for running the harness
- a concise baseline markdown report or regeneration workflow
- tests validating corpus shape and fixture references

Out of scope:

- changing tool prompts or schemas
- adding new tools
- model comparison mode
- pass/fail grading against expectations
- running eval tasks in parallel
- storing large raw result directories in git

## Proposed Files

```text
eval/
├── README.md
├── corpus.yaml
├── baseline.md
└── fixtures/
    ├── python-package/
    ├── json-config/
    ├── docs-site/
    └── simple-cli/

plans/phase67-eval-corpus-baseline.md
tests/test_eval_corpus.py
```

If `eval/` conflicts with package tooling or naming, use `tests/fixtures/eval/`
for fixture repos and keep only `eval/corpus.yaml`, `eval/README.md`, and
`eval/baseline.md` at top level.

## Corpus Design

Start with 10-20 deterministic tasks. Keep fixture repos tiny so runs are cheap
and diffs are easy to inspect.

Recommended task mix:

| Task type | What it measures |
|---|---|
| single-file edit | `Read` -> `Edit` baseline |
| multi-file edit | whether `ApplyPatch` / `ReplaceMany` is chosen |
| targeted line edit | whether `ReadRange` is used |
| JSON config update | whether `JsonEdit` is chosen |
| failing-test fix | tool sequence and shell usage |
| add test coverage | edit + test loop behavior |
| package metadata lookup | `PackageInfo` usefulness |
| symbol lookup | `CodeSymbols` usefulness |
| docs update | markdown edit path |
| git inspection | `GitStatus` / `GitDiff` use |
| shell-avoidance task | whether safe read tools replace avoidable `Bash` |

Expectations should stay advisory:

```yaml
tasks:
  - id: "json-config-add-timeout"
    repo: "eval/fixtures/json-config"
    prompt: "Add a 30 second request timeout to config/app.json."
    expect:
      max_turns: 6
      tools_used: ["Read", "JsonEdit"]
      avoids: ["Bash"]
```

## Baseline Report

Prefer committing `eval/baseline.md` rather than raw generated JSON. The report
should summarize:

- run date and commit hash
- model used
- number of tasks
- mean/median turns
- total and per-task estimated cost
- tool-call distribution
- top divergent expectations
- notable repeated sequences, for example `Read -> Read -> Edit`
- initial recommendations for Phase 68

Raw `eval-results/` output should stay ignored unless there is a specific reason
to preserve a small fixture result.

## Tests

Add `tests/test_eval_corpus.py` covering:

1. `eval/corpus.yaml` parses through the existing `EvalCorpus` loader.
2. Task IDs are unique.
3. Every `repo` path exists.
4. Fixture repos contain at least one tracked/source file.
5. Advisory expectation keys are from an allowed set.
6. `eval/README.md` mentions the command used to regenerate results.

Avoid live model calls in CI. These tests validate corpus hygiene only.

## Verification

Run:

```bash
python -m pytest tests/test_eval.py tests/test_eval_corpus.py
python -m ruff check .
python -m ruff format --check .
python -m mypy
```

Manual baseline command:

```bash
python -m d2c eval eval/corpus.yaml --out-dir eval-results
python -m json.tool eval-results/summary.json
```

The manual command requires `DEEPSEEK_API_KEY`; it should not run in normal CI.

## Acceptance Criteria

- `eval/corpus.yaml` contains at least 10 tasks.
- All task repos are local fixture paths and validated by tests.
- `eval/README.md` documents how to run and inspect evals.
- `eval/baseline.md` records a concise baseline or clearly documents how to
  generate one if no API key is available during implementation.
- Existing gates remain green.
- `plans/backlog-reconciliation.md` can point to the corpus/baseline as the
  source of truth for Phase 68 priorities.

## Expected Outcome

The project moves from intuition-driven tool planning to measurement-driven
iteration. Phase 68 can then tune `ApplyPatch`, `ReplaceMany`, `ReadRange`, and
inspection-tool descriptions based on actual tool-use data instead of guessing.
