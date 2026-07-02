# d2c eval corpus

A small, deterministic corpus of task prompts run through the Phase 66
headless eval harness (`d2c.eval`, `python -m d2c eval`). It exists to
answer empirical tool-usage questions (does the model reach for
`ApplyPatch` on a multi-file edit? does it use `ReadRange` for a targeted
line fix instead of a full `Read`?) with data instead of guesswork — see
`plans/phase66-eval-harness.md` and `plans/phase67-eval-corpus-baseline.md`.

## Layout

- `corpus.yaml` — the task list. Each task has a `prompt`, a `repo` (a
  fixture directory below, or `.` for the d2c repo itself), and an
  advisory `expect` block (`max_turns`, `tools_used`, `avoids`,
  `preferred_tool`, `tolerate_verification_failure`). `expect` never fails
  a task — it only produces a `divergences` entry in the per-task report.
  `tolerate_verification_failure: true` (Phase 68) additionally lets a task
  whose only failure is a trailing verification tool error (e.g. running a
  whole suite that trips an unrelated known-failing fixture test) still
  count as successful, recording the swallowed error as a `note`.
- `fixtures/` — tiny throwaway repos the tasks run against:
  - `python-package/` — a package with a docstring-free function, an
    unused-but-correct helper, a renamed-function target, and a failing
    test (intentional — see `failing-test-fix` in `corpus.yaml`).
  - `json-config/` — a JSON config file missing a `timeout` key.
  - `docs-site/` — markdown docs with a known typo on a known line.
  - `simple-cli/` — a small argparse CLI missing a flag.
- `baseline.md` — the Phase 67 baseline report (the "before" snapshot at
  commit `41131a2`).
- `phase68-results.md` — the Phase 68 before/after report (eval-guided
  tool tuning: success semantics + `ApplyPatch` discoverability).

Fixture edits made by a live eval run are **not** committed back — each
run should start from a clean fixture tree (`git status` before/after,
or `git checkout -- eval/fixtures` between runs).

## Running the harness

Requires `DEEPSEEK_API_KEY` (or `.env`) and a trusted repo — this makes
real model calls and is never run in CI.

```bash
git checkout -- eval/fixtures   # start from a clean fixture tree
python -m d2c eval eval/corpus.yaml --out-dir eval-results --trust
python -m json.tool eval-results/summary.json
```

Run from the repository root so the corpus's relative `repo` paths
resolve correctly. `eval-results/` is gitignored — it's regenerated
output, not something to commit.

Per-task reports land at `eval-results/<task-id>.json` (turns, tool
counts, tool sequence, tokens, cost estimate, compaction count, success,
divergences). `eval-results/summary.json` aggregates across the whole
corpus (mean/median turns, tool-call distribution and share, totals).

To update `baseline.md`, run the harness, review
`eval-results/summary.json` and the per-task reports, then hand-write a
concise summary (see the template at the top of `baseline.md`) — don't
paste raw JSON into the baseline doc.

## Corpus hygiene tests

`tests/test_eval_corpus.py` validates the corpus shape (unique task IDs,
fixture repo paths exist, advisory `expect` keys are from the known set,
this README documents the run command) without making any model calls,
so it runs in normal CI.
