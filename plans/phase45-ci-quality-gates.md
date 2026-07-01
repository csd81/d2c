# Phase 45: CI and quality gates

**Priority:** HIGHEST (Prevent regressions after production hardening)

## Context

Phase 44 added observability and audit logging. The next production-grade step is to prevent
regressions from entering the repo at all.

This phase adds automated quality gates for every push/PR:

- formatting/linting
- type checking
- security linting
- dependency vulnerability scanning
- tests
- package build

## Goal

Create a repeatable CI pipeline that answers:

```text
Can a fresh machine install this repo, run checks, run tests, and build a package?
```

## Scope

In scope:

- GitHub Actions workflow
- `ruff`
- `mypy` or `pyright`
- `bandit`
- `pip-audit`
- `pytest`
- `python -m build`
- config in `pyproject.toml`
- README/contributor docs for local checks

Out of scope:

- massive type-hint refactor
- publishing to PyPI
- external telemetry
- live Tavily/DeepSeek tests in CI
- Windows/macOS CI unless explicitly chosen

## Tool Choices

### Ruff

Purpose:

- linting
- import cleanup
- formatting check

Commands:

```bash
ruff check .
ruff format --check .
```

Start with a practical rule set. Do not enable every strict rule at once.

### Type Checker

Pick one:

```text
mypy    - Python-native, common, easier to stage gradually
pyright - stricter/faster, closer TypeScript-like developer experience
```

Recommendation for Phase 45:

```text
Use mypy first if pyproject integration is already Python-only.
Use pyright if you want stricter checks and better editor parity.
```

Start with core modules only if full-repo typing is too noisy:

```bash
mypy src/d2c/config.py src/d2c/permissions src/d2c/tools src/d2c/observability.py
```

Later phases can expand to all of `src/d2c`.

### Bandit

Purpose:

- security static analysis

Command:

```bash
bandit -r src/d2c
```

Expect some intentional findings around subprocess/shell execution. Suppress only with a clear
reason.

### pip-audit

Purpose:

- dependency vulnerability scan

Command:

```bash
pip-audit
```

If a dependency has no fix available, document an ignore with CVE/advisory id and reason.

### Build

Purpose:

- package hygiene
- ensure wheel/sdist build from a clean checkout

Command:

```bash
python -m build
```

## Files to Create/Modify

1. CREATE `.github/workflows/ci.yml`
   - install Python
   - install package with dev dependencies
   - run quality gates
   - run tests
   - build package

2. MODIFY `pyproject.toml`
   - add dev dependencies:
     - `ruff`
     - `mypy` or `pyright`
     - `bandit`
     - `pip-audit`
     - `build`
   - add tool config sections

3. OPTIONAL CREATE `mypy.ini` or `pyrightconfig.json`
   - only if not using `pyproject.toml`

4. MODIFY `README.md`
   - add local quality-check commands

5. OPTIONAL CREATE `CONTRIBUTING.md`
   - short contributor check instructions

## CI Workflow Shape

Suggested first workflow:

```yaml
name: ci

on:
  push:
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: python -m pip install -U pip
      - run: pip install -e ".[dev]"
      - run: ruff check .
      - run: ruff format --check .
      - run: mypy src/d2c
      - run: bandit -r src/d2c
      - run: pip-audit
      - run: pytest
      - run: python -m build
```

If `mypy src/d2c` is too noisy, use a narrower target and document the staged expansion.

## Staged Adoption

Use this order:

1. Add `ruff` and formatting gate.
2. Add `pytest`.
3. Add `python -m build`.
4. Add `pip-audit`.
5. Add `bandit` with justified suppressions.
6. Add typing gate in staged mode.

Do not block the phase on making the entire repo perfectly typed if that turns into a large refactor.

## Security Suppression Rules

If suppressing Bandit findings:

- suppress locally, not globally, when possible
- include a short justification
- only suppress intentional shell/subprocess paths
- never suppress hardcoded secret findings without removing the secret

Example:

```python
# nosec B602: BashTool intentionally executes user-approved shell commands.
```

## Tests / Verification

Run locally:

```bash
ruff check .
ruff format --check .
mypy src/d2c
bandit -r src/d2c
pip-audit
pytest
python -m build
```

If typing is staged:

```bash
mypy src/d2c/config.py src/d2c/observability.py src/d2c/permissions src/d2c/tools
```

## Acceptance Criteria

- CI workflow exists and runs on push/PR.
- Fresh CI install uses `pip install -e ".[dev]"`.
- `ruff check` passes.
- `ruff format --check` passes.
- test suite passes.
- package build succeeds.
- dependency audit runs.
- security lint runs with only justified suppressions.
- type checking runs at least on selected core modules.
- README documents local check commands.

## Expected Outcome

Every future change gets checked automatically for style, tests, package health, security risks, and
typed core contracts. This makes the recent production-hardening work durable instead of relying on
manual discipline.
