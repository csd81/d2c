# Contributing to d2c

Thanks for contributing! There is no automatic GitHub CI — the quality gate runs
locally, in two tiers:

```bash
./scripts/check_fast.sh [tests/...]   # inner loop: lint + format + types (+ targeted tests)
./scripts/check_release.sh            # before push / release / phase: the full suite
```

## Setup

```bash
pip install -e ".[dev]"
```

## Local quality gate

Two scripts, matched to how often you run them:

- **`./scripts/check_fast.sh`** — the inner-loop check for normal work: `ruff
  check`, `ruff format --check`, `mypy`, and (if you pass paths) the targeted
  tests for what you touched, e.g. `./scripts/check_fast.sh tests/test_eval.py`.
- **`./scripts/check_release.sh`** — run before pushing, releasing, or completing
  a phase. A superset of the fast checks plus the heavy ones: the full test
  suite, `bandit`, advisory `pip-audit`, a clean `dist/` build, and `twine
  check`.

Both use `python -m pytest` (not bare `pytest`) for stable import behavior, exit
non-zero on the first failure, and need no API credentials. `check_release.sh`
clears `dist/` before building so `twine check` isn't fooled by stale artifacts.

To run steps individually:

```bash
python -m ruff check .                          # lint
python -m ruff format --check .                 # formatting (use `ruff format .` to fix)
python -m mypy                                  # types — staged clean modules (pyproject [tool.mypy].files)
python -m bandit -c pyproject.toml -r src/d2c   # security lint
python -m pip_audit                             # dependency CVE scan (advisory; non-blocking)
python -m pytest                                # tests
python -m build                                 # package build
python -m twine check dist/*                    # artifact validation
```

Quick auto-fix pass:

```bash
ruff check --fix . && ruff format .
```

## Conventions

- **Async throughout** — the loop and all tool `execute()` methods are `async`. Mark new async
  tests with `@pytest.mark.asyncio` (there is no `asyncio_mode=auto`).
- **Paper-concept names** stay camelCase (`queryLoop`, `assembleToolPool`, `resolve_permission_decision`),
  even though surrounding Python is snake_case.
- **Safety invariants** — Write/Edit require a prior Read; permission decisions fail closed; `ASK`
  never auto-executes; audit logs are redacted. Don't add tools or paths that bypass these.
- **Typing is staged** — if you add a new module, try to keep it clean under `mypy` and add it to
  `[tool.mypy].files`. Don't do a repo-wide typing refactor in one PR.
- **Bandit suppressions** — only for intentional shell/subprocess or fail-open observability paths,
  with a short justification (see `[tool.bandit].skips` in `pyproject.toml`).

## Tests

Each phase adds `tests/test_phaseNN*.py`. Add focused tests for new behavior; prefer deterministic,
network-free tests (mock providers/HTTP). See `plans/` for the design docs behind each subsystem.
