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
  - `json-config/` — a JSON config (`app.json`, with a nested `logging`
    block) and a `settings.yaml` (YAML-edit target).
  - `docs-site/` — markdown docs with known typos on known lines
    (`guide.md`, and `reference.md` for the v2 targeted-line-fix task).
  - `simple-cli/` — a small argparse CLI (+ `README.md` for the paired
    docs/code change).
  - `refactor-mini/` — a literal (`TIMEOUT = 30`) repeated across
    `config.py`, `README.md`, and `tests/test_config.py` (its own tests
    pass) for the repeated-literal update task.
- `baseline.md` — the Phase 67 baseline report (the "before" snapshot at
  commit `41131a2`).
- `phase87-corpus-v2.md` — the corpus v2 expansion (Phase 87): 13 → 24
  tasks with broader multi-file/refactor coverage; no live run performed.
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

## Batch mode (model-call only, Phase 85)

`--batch` runs *batchable* tasks through DeepSeek's Batch API (cheaper, less
traffic-control sensitive) instead of the live agent loop:

```bash
python -m d2c eval eval/corpus.yaml --batch --dry-run --out-dir eval-results/batch  # generate JSONL only
python -m d2c eval eval/corpus.yaml --batch --out-dir eval-results/batch            # submit + poll (needs key)
```

Batch is **not** equivalent to the live agent eval: provider batch jobs run a
single prompt→response call and cannot execute local tools (Bash/Edit/ApplyPatch)
or mutate fixtures. Only tasks marked `batchable: true` in the corpus are
submitted; every other task is recorded as `{"status": "skipped", "reason":
"task requires local tool execution"}`. Add `batch_prompt:` to override the
prompt used for the batch call. Results (`summary.json` with `mode: batch`, plus
`batch-input.jsonl`, `batch-output.jsonl`, and per-task JSON) land under
`--out-dir`. The default `d2c eval` (no `--batch`) remains the live agent runner.

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
