# Eval baseline

A measured baseline of how the default d2c agent handles the `corpus.yaml`
tasks. Use it as the "before" picture when tuning tool descriptions in a
later phase — regenerate and diff after changes.

> **Phase 68 update:** this doc is the Phase 67 "before" snapshot (commit
> `41131a2`). The first tuning pass and its before/after measurements live in
> `phase68-results.md`.

## Run metadata

| Field | Value |
| --- | --- |
| Date | 2026-07-02 |
| Commit | `cbce098` (working tree, + uncommitted Phase 67) |
| Backend / model | DeepSeek, `deepseek-v4-pro` (default) |
| Permission mode | `bypass` (`--trust`) |
| Max turns | 15 |
| Tasks | 13 |

**How it was generated.** Each task ran against its own pristine copy of
its fixture repo, in parallel (subprocess-isolated), then the per-task
JSONs were merged into `eval-results/summary.json`. The tasks are mutually
independent, so this is equivalent to the documented sequential command
(`python -m d2c eval eval/corpus.yaml --out-dir eval-results --trust`) but
faster, and it guarantees every task starts from a clean fixture tree
rather than seeing an earlier task's edits. The checked-in fixtures are
left untouched (`git status` clean before and after).

## Summary

| Metric | Value |
| --- | --- |
| Success | 12 / 13 |
| Mean turns | 3.62 |
| Median turns | 3.0 |
| Total input tokens | 80,424 |
| Total output tokens | 7,914 |
| Est. cost (USD) | ~$0.15 |
| Compactions | 0 |
| Divergences (advisory) | 1 |

## Tool-call distribution

| Tool | Calls | Share |
| --- | --- | --- |
| Read | 16 | 37.2% |
| Edit | 11 | 25.6% |
| Bash | 4 | 9.3% |
| ListDir | 2 | 4.7% |
| TaskUpdate | 2 | 4.7% |
| ReadRange | 1 | 2.3% |
| JsonEdit | 1 | 2.3% |
| TaskCreate | 1 | 2.3% |
| PackageInfo | 1 | 2.3% |
| CodeSymbols | 1 | 2.3% |
| GitStatus | 1 | 2.3% |
| GitDiff | 1 | 2.3% |
| Grep | 1 | 2.3% |

`Read` + `Edit` are ~63% of all tool calls. The specialized read/inspect
tools (`ReadRange`, `PackageInfo`, `CodeSymbols`, `GitStatus`, `Grep`,
`JsonEdit`, `ListDir`) do get reached for when a task points squarely at
them, but `Read`/`Edit` remain the default reflex.

## Per-task results

| Task | ok | turns | tool sequence |
| --- | --- | --- | --- |
| package-metadata-lookup | ✅ | 2 | PackageInfo |
| symbol-lookup | ✅ | 2 | CodeSymbols |
| symbol-search-grep | ✅ | 2 | Grep |
| shell-avoidance-list-fixtures | ✅ | 2 | ListDir |
| single-file-edit-add-docstring | ✅ | 3 | Read, Edit |
| targeted-line-edit-fix-typo | ✅ | 3 | ReadRange, Edit |
| git-inspection-status | ✅ | 3 | GitStatus, GitDiff, ListDir |
| docs-update-add-section | ✅ | 4 | Read, Edit, Read |
| json-config-add-timeout | ✅ | 4 | Read, JsonEdit, Read |
| multi-file-edit-rename-function | ✅ | 5 | Read, Read, Edit×4, Read, Read |
| simple-cli-add-flag | ✅ | 5 | Read, Edit, Read, Bash, Bash |
| add-test-coverage | ❌ | 5 | Read, Read, Edit, Edit, Read, Bash |
| failing-test-fix | ✅ | 7 | TaskCreate, Read, Read, TaskUpdate, Edit, Bash, TaskUpdate |

## Notable observations

- **The one "failure" is a false negative, not a bad edit.**
  `add-test-coverage` ended on a `Bash` call that exited non-zero, which
  the harness scores as a failed run (`success = ... not (saw_tool and
  last_tool_error)`). The model correctly added a `subtract` test, then
  verified by running the *whole* suite — which still contains the
  intentionally-broken `test_multiply` (the `failing-test-fix` target).
  Running the full suite conflates an unrelated pre-existing failure with
  the task's own outcome. Worth noting for corpus design: verify-by-Bash
  tasks over a repo that also ships a known-failing test will look failed.

- **The one advisory divergence is `multi-file-edit-rename-function`.**
  The corpus `expect`ed `preferred_tool: ApplyPatch` for a cross-file
  rename; the model instead did four separate `Edit` calls. This is
  exactly the kind of tool-selection gap the eval exists to surface — a
  candidate for a Phase-68 tweak to `ApplyPatch`'s description (make its
  multi-file/multi-hunk advantage more prominent) or to reconsider whether
  four scoped edits is actually the preferable behavior here.

- **Specialized-tool tasks are cheap and correct.** The four single-tool
  tasks (`PackageInfo`, `CodeSymbols`, `Grep`, `ListDir`) each resolved in
  2 turns with the intended tool and no `Bash` fallback — the
  `avoids: [Bash]` expectations all held. `targeted-line-edit-fix-typo`
  reached for `ReadRange` (not a full `Read`) as hoped.

- **No compaction fired** on any task — these are all small, so the
  context pipeline isn't exercised here (expected).

## Suggested follow-ups (for a later tool-tuning phase)

1. Sharpen `ApplyPatch`'s description so multi-file/multi-hunk edits
   prefer it over N sequential `Edit`s — then re-run and check whether the
   `multi-file-edit-rename-function` divergence clears.
2. Decide whether "run the full test suite" is the behavior we want for
   add-a-test tasks, or whether the model should scope the verification to
   the new test; either way, adjust the corpus/fixtures so a correct edit
   isn't scored as a failure by an unrelated pre-existing test.
3. Watch the `Read`/`Edit` share over time — if a tuning change is meant
   to push work toward specialized tools, this baseline's ~63% is the
   number to beat.

## Regenerating

See `README.md`. In short, from the repo root with `DEEPSEEK_API_KEY` set:

```bash
git checkout -- eval/fixtures
python -m d2c eval eval/corpus.yaml --out-dir eval-results --trust
python -m json.tool eval-results/summary.json
```

Then review `eval-results/` and update the tables above. `eval-results/`
is gitignored; don't commit it.
