# Eval corpus v2 (Phase 87)

Broadens the live corpus with multi-file/refactor tasks so future tool-tuning is
measured against more than the single rename task the Phase 67/68 signal rested
on. The Phase 67 baseline (`baseline.md`) is left untouched as history.

## Size

| | Phase 67 | v2 (Phase 87) |
| --- | --- | --- |
| Tasks | 13 | 24 (+11) |

## New task categories (11 tasks, all `v2-*`)

| Task | Theme | Directional signal |
| --- | --- | --- |
| `v2-cross-file-rename-subtract` | cross-file symbol rename | `ApplyPatch` |
| `v2-import-move-multiply` | move a function to a new module + fix imports | `ApplyPatch` |
| `v2-repeated-literal-update` | change a literal repeated across code/docs/tests | `ApplyPatch` |
| `v2-targeted-line-fix-noise` | fix one known line in a larger doc | `ReadRange` + `Edit` |
| `v2-add-edge-case-test` | add one focused test | `Read` + `Edit` (tolerates the fixture's known failure) |
| `v2-json-nested-edit` | change a nested JSON key | `JsonEdit`, avoid `Bash` |
| `v2-yaml-add-field` | add a YAML field | `Edit` (no YAML-specific tool — gap recorded below) |
| `v2-docs-code-paired-change` | coordinated code + README change | `ApplyPatch` |
| `v2-search-before-edit` | find references before editing | `Grep` |
| `v2-noop-inspection` | confirm an already-complete state, make no edits | read/search only (avoids `Edit`/`Write`/`ApplyPatch`/`Bash`) |
| `v2-explain-add-function` | pure explanation (also `batchable: true`) | avoid `Bash` |

`ApplyPatch` is now the `preferred_tool` for **four** distinct tasks (rename,
import-move, repeated-literal, docs+code) instead of one — the point of v2.

## New / extended fixtures

- `eval/fixtures/refactor-mini/` (new): `config.py` (`TIMEOUT = 30`), `README.md`,
  and `tests/test_config.py` — the same literal in three files for the
  repeated-literal task. Its own tests **pass** (no intentional failure).
- `eval/fixtures/json-config/config/app.json`: gained a nested `logging.level`.
- `eval/fixtures/json-config/config/settings.yaml` (new): the YAML-edit target.
- `eval/fixtures/simple-cli/README.md` (new): docs half of the paired change.
- `eval/fixtures/docs-site/docs/reference.md` (new): a longer doc with a known
  typo ("termiate") on line 15 for the targeted-line-fix task.

Fixtures stay tiny, dependency-free, and deterministic. The only intentional
failure remains `python-package`'s `test_multiply` (the multiply bug), which the
`tolerate_verification_failure` flag on `v2-add-edge-case-test` accounts for.

## Recorded gaps

- **No YAML-specific edit tool.** `v2-yaml-add-field` expects `Read` + `Edit`; if
  a future phase adds a YAML structured-edit tool, switch its `preferred_tool`
  and re-measure.

## Batchability

- `v2-explain-add-function` is `batchable: true` with a self-contained
  `batch_prompt` (batch jobs can't read fixtures). All other v2 tasks require
  local tools and run live-only. Batch results are not comparable to live runs.

## Live baseline

**Not run during implementation** — Phase 87 is corpus/fixture content plus
CI-safe hygiene tests. To measure v2 against the tuned tools, run the live harness
(needs `DEEPSEEK_API_KEY`; see `eval/README.md`):

```bash
python -m d2c eval eval/corpus.yaml --out-dir eval-results/phase87 --trust
```

Fixture mutations from a live run must not be committed (Phase 67 uses isolated
copies; verify `git status` after any local run).
