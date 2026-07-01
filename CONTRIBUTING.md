# Contributing to d2c

Thanks for contributing! d2c uses automated quality gates (CI: `.github/workflows/ci.yml`).
Run them locally before pushing.

## Setup

```bash
pip install -e ".[dev]"
```

## Local checks (same as CI)

```bash
ruff check .                          # lint
ruff format --check .                 # formatting (use `ruff format .` to fix)
mypy                                  # types — staged clean modules (pyproject [tool.mypy].files)
bandit -c pyproject.toml -r src/d2c   # security lint
pip-audit                             # dependency CVE scan (advisory; non-blocking in CI)
pytest                                # tests
python -m build                       # package build
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
