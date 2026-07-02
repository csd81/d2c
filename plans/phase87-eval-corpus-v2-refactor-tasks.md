# Phase 87: Eval corpus v2 refactor tasks

**Priority:** HIGH (better evidence for future tool/workflow tuning)

## Context

The Phase 67 corpus gave the project its first measured baseline, and Phase 68
used it to tune tool descriptions. That worked, but the strongest ApplyPatch
signal rested heavily on one multi-file rename task. Since then, the provider
runtime and UX surfaces have stabilized:

- DeepSeek model/pricing/thinking/error handling are aligned.
- Batch eval mode exists for model-call-only experiments.
- The live eval runner remains the source of truth for tool-using agent behavior.

The next high-ROI eval work is to broaden the live corpus with more deterministic
multi-file edit/refactor tasks so future tool-description changes are measured
against more than one rename scenario.

## Goal

Expand `eval/corpus.yaml` and fixtures with a v2 set of small, deterministic
refactor/edit tasks that better exercise:

```text
ApplyPatch
Edit
ReadRange
Grep
CodeSymbols
JsonEdit / structured edits
Bash verification
```

Keep the corpus cheap to run, easy to inspect, and CI-safe.

## Scope

In scope:

- add small fixture files/repos or extend existing fixtures
- add 8-12 new live-agent tasks
- strengthen advisory expectations for refactor/edit workflows
- add hygiene tests for new fixtures/tasks
- update eval README and baseline notes
- optionally mark a few model-call-only tasks as `batchable`
- changelog note

Out of scope:

- changing the eval harness architecture
- changing success semantics from advisory to hard pass/fail
- running live model calls in CI
- replacing the Phase 67 baseline
- adding large real-world repos
- full release gate

## Task Themes

Add tasks that are deterministic, tiny, and behavior-preserving.

Recommended task additions:

1. **Cross-file symbol rename**
   Rename a helper used from multiple files and exports. Prefer `ApplyPatch`.

2. **Import move**
   Move one function from `greeter/util.py` to a new module and update imports.
   Prefer `ApplyPatch`; verify with targeted tests.

3. **Repeated literal/config update**
   Change a CLI flag or config key repeated in docs + code + tests. Prefer
   `ApplyPatch` or structured edits.

4. **Targeted line fix with nearby noise**
   Fix one known line in a larger markdown/config file. Prefer `ReadRange`.

5. **Add test for edge case**
   Add a focused test for an existing function without fixing unrelated known
   fixture failures. Use `tolerate_verification_failure` only if needed.

6. **JSON nested edit**
   Add or change a nested key in JSON while preserving valid formatting. Prefer
   `JsonEdit`.

7. **YAML edit**
   Add a small YAML field using the best available tool. If no YAML-specific
   tool exists, expect `Read` + `Edit` and record that gap.

8. **Docs/code paired change**
   Change a CLI option behavior and update README/help text in the fixture.
   Prefer coordinated multi-file edit.

9. **Symbol search before edit**
   Ask for a change where references must be found first. Prefer `Grep` or
   `CodeSymbols` before editing.

10. **No-op inspection**
    Ask whether a rename is needed when it is already complete. Expect read/search
    only, no edits.

## Fixture Strategy

Prefer extending current tiny fixtures before adding new ones:

```text
eval/fixtures/python-package
eval/fixtures/simple-cli
eval/fixtures/json-config
eval/fixtures/docs-site
```

Add a new fixture only if it reduces confusion, for example:

```text
eval/fixtures/refactor-mini
```

Rules:

- fixtures stay small enough to understand in one screen
- no external dependencies
- deterministic tests
- intentional failing tests are documented
- generated eval outputs remain ignored
- fixture mutations from live runs must not be committed

## Advisory Expectations

Use expectations to measure behavior without overfitting:

```yaml
expect:
  max_turns: 10
  preferred_tool: "ApplyPatch"
  tools_used: ["Grep"]
  avoids: ["Bash"]
```

Guidance:

- use `preferred_tool` for directional tool-discovery signals
- use `tools_used` only when the tool is genuinely expected
- use `avoids` for shell-avoidance tasks
- keep `max_turns` loose enough to catch regressions without penalizing harmless
  exploration
- use `tolerate_verification_failure` sparingly and document why

## Batchability

Most v2 tasks will be live-agent-only because they require local tools.

Optional:

- mark one or two pure inspection/explanation tasks `batchable: true`
- add `batch_prompt` only when the live prompt is tool-oriented and unsuitable
  for a single model response

Do not compare batch results to live tool behavior.

## Baseline Reporting

Do not overwrite Phase 67 history.

Add one of:

```text
eval/phase87-corpus-v2.md
```

or append a clearly dated section to:

```text
eval/baseline.md
```

Report:

- old task count vs new task count
- new task categories
- expected tool signals added
- any intentionally known fixture failures
- live baseline command
- whether a live baseline was run or deferred

If no live run is performed during implementation, say so explicitly.

## Files to Inspect / Modify

Likely:

```text
eval/corpus.yaml
eval/README.md
eval/baseline.md
eval/fixtures/python-package/**
eval/fixtures/simple-cli/**
eval/fixtures/json-config/**
eval/fixtures/docs-site/**
tests/test_eval_corpus.py
CHANGELOG.md
```

Optional:

```text
eval/fixtures/refactor-mini/**
eval/phase87-corpus-v2.md
tests/test_phase87_eval_corpus_v2.py
```

## Tests

Add or update CI-safe tests for:

1. corpus parses successfully
2. all task IDs are unique
3. new fixture paths exist
4. new task prompts are non-empty and deterministic
5. advisory expectation keys are known
6. every new fixture has a minimal smoke command if applicable
7. fixture tests pass except documented intentional failures
8. batchable tasks, if any, have batch-suitable prompts
9. README documents the expanded corpus and run command
10. no `eval-results/` artifacts are tracked

No test should make a model call.

## Verification

Fast only:

```bash
./scripts/check_fast.sh
python -m pytest tests/test_eval.py tests/test_eval_corpus.py tests/test_phase85_eval_batch.py
```

Fixture smoke examples:

```bash
python -m pytest eval/fixtures/python-package/tests -q
python eval/fixtures/simple-cli/cli.py --help
python -m json.tool eval/fixtures/json-config/config/app.json
```

Optional live eval only when explicitly requested:

```bash
python -m d2c eval eval/corpus.yaml --out-dir eval-results/phase87 --trust
```

Do not run the full release gate unless explicitly requested.

## Risks

| Risk | Mitigation |
| --- | --- |
| Corpus gets too large/expensive | Add 8-12 focused tasks, keep fixtures tiny |
| Expectations overfit one model run | Keep expectations advisory and directional |
| Fixture failures create false negatives | Document intentional failures; use tolerance sparingly |
| New tasks duplicate old signals | Track task theme/category in the report |
| Live eval mutates checked-in fixtures | Preserve Phase 67 isolated-copy behavior; verify git status after local runs |

## Acceptance Criteria

- Corpus has broader multi-file/refactor coverage than Phase 67.
- ApplyPatch signal is represented by multiple distinct tasks.
- ReadRange/structured-edit/search signals are represented by targeted tasks.
- Existing eval behavior and task semantics remain compatible.
- Eval docs explain v2 coverage and how to run it.
- CI-safe hygiene tests cover the expanded corpus.
- No live model calls are required for tests.
- Fast checks pass.

## Expected Outcome

Future tool-description and workflow changes can be evaluated against a broader,
less fragile corpus. The project gets better evidence for whether changes improve
real edit/refactor behavior instead of optimizing for a single rename task.
