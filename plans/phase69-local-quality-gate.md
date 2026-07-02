# Phase 69: Local quality gate command

**Priority:** HIGH (preserve release confidence after removing GitHub CI)

## Context

The GitHub Actions `ci` workflow was removed because push/PR CI had become noisy
and repeatedly red even when the local gate suite was green. The project still
needs a repeatable quality gate before commits and releases.

The local-vs-CI mismatch also exposed a concrete lesson: `python -m pytest`
and bare `pytest` can differ in import behavior. The project should document
and automate the exact local command sequence to run.

## Goal

Provide one reproducible local command that runs the full quality gate suite:

- lint
- format check
- type check
- security lint
- dependency audit
- tests
- package build
- artifact validation

## Scope

In scope:

- a shell script under `scripts/`
- README/CONTRIBUTING documentation
- optional changelog note
- tests or smoke checks for the script if practical

Out of scope:

- restoring GitHub Actions CI
- changing test behavior
- adding pre-commit hooks
- publishing to PyPI
- replacing `pip-audit` advisory behavior with a hard failure

## Proposed File

```text
scripts/quality_gate.sh
```

Optional docs:

```text
README.md
CONTRIBUTING.md
CHANGELOG.md
```

## Script Design

Use strict shell mode:

```bash
#!/usr/bin/env bash
set -euo pipefail
```

Run from the repo root even when invoked from another directory:

```bash
cd "$(dirname "$0")/.."
```

Recommended commands:

```bash
python -m ruff check .
python -m ruff format --check .
python -m mypy
python -m bandit -c pyproject.toml -r src/d2c
python -m pip_audit || true
python -m pytest
rm -rf dist
python -m build
python -m twine check dist/*
```

Notes:

- Use `python -m pytest`, not bare `pytest`, to keep import behavior stable.
- Keep `pip-audit` advisory unless the project explicitly decides dependency
  CVEs should block commits.
- Remove `dist/` before building so `twine check` cannot pass or fail because
  of stale artifacts.
- Do not require live model/API credentials.

## Documentation

Add a short section to `README.md` or `CONTRIBUTING.md`:

```bash
./scripts/quality_gate.sh
```

Explain that this is the replacement for automatic GitHub CI and should be run
before pushing phase commits or release commits.

## Tests / Verification

Run:

```bash
chmod +x scripts/quality_gate.sh
./scripts/quality_gate.sh
```

Also verify:

```bash
git status --short
```

Expected caveat: `dist/` may be regenerated and ignored. The script should not
modify tracked files.

## Acceptance Criteria

- `scripts/quality_gate.sh` exists and is executable.
- The script runs the full local quality gate suite.
- It uses `python -m pytest`.
- It clears `dist/` before package build.
- Docs tell contributors to run it before pushing.
- The script completes successfully in the current dev environment.

## Expected Outcome

The project keeps a single, documented quality standard even without automatic
GitHub CI. Future phases can run the same local gate and report one command
instead of manually listing every tool invocation.
