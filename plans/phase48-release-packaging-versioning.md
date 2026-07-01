# Phase 48: Release packaging and versioning workflow

**Priority:** HIGH (Reproducible releases)

## Context

Phases 34-47 made `d2c` substantially more production-ready:

- runtime safety wiring
- shell permission hardening
- WebSearch
- hooks
- tool breadth
- observability
- CI/quality gates
- security regression tests
- doctor diagnostics

The next production-grade step is reproducible release packaging: versioning, changelog, package
metadata, release checks, and optional TestPyPI/PyPI workflow.

## Goal

Make releases predictable and auditable:

1. Add a single source of truth for package version.
2. Add `python -m d2c --version`.
3. Verify wheel/sdist contents.
4. Add a changelog and release checklist.
5. Add a repeatable release workflow.
6. Optionally support TestPyPI dry runs.

## Scope

In scope:

- version metadata
- CLI `--version`
- changelog
- release checklist
- wheel/sdist verification
- package metadata review
- optional GitHub Actions release workflow
- optional TestPyPI documentation

Out of scope:

- publishing a real public release unless explicitly requested
- changing package name
- major repository restructure
- binary distribution
- Docker image publishing

## Files to Create/Modify

1. MODIFY `src/d2c/__init__.py`
   - define `__version__`

2. MODIFY `src/d2c/main.py`
   - add `--version`

3. MODIFY `pyproject.toml`
   - use static version or dynamic version consistently
   - verify package metadata
   - verify package-data config

4. CREATE `CHANGELOG.md`
   - start with unreleased/current version

5. CREATE `RELEASE.md` or `docs/release.md`
   - release checklist and commands

6. OPTIONAL CREATE `.github/workflows/release.yml`
   - build artifacts on tags
   - optionally publish to TestPyPI/PyPI using trusted publishing or secrets

7. MODIFY `README.md`
   - mention `--version`
   - optionally link release docs

8. CREATE/UPDATE tests
   - verify `--version`
   - verify version string matches package metadata if practical

## Version Strategy

Use one source of truth.

Recommended simple strategy:

```python
# src/d2c/__init__.py
__version__ = "0.1.0"
```

Then in `pyproject.toml`, either:

```toml
[project]
version = "0.1.0"
```

or dynamic:

```toml
[project]
dynamic = ["version"]
```

If using dynamic version, configure setuptools to read `d2c.__version__`.

Keep it simple unless the repo already uses another pattern.

## CLI Version

Add:

```bash
python -m d2c --version
```

Expected output:

```text
d2c 0.1.0
```

This should not require API keys, config loading, trust prompts, or model initialization.

## Changelog

Create `CHANGELOG.md`:

```markdown
# Changelog

## Unreleased

## 0.1.0 - YYYY-MM-DD

- Core agent loop
- Permission system
- Compaction
- Persistence
- MCP
- WebSearch
- Observability
- Doctor command
```

Keep it factual. Do not list every internal phase unless useful.

## Release Checklist

Create `docs/release.md` or `RELEASE.md`:

```bash
git status --short
python -m d2c --doctor
ruff check .
ruff format --check .
mypy ...
bandit -c pyproject.toml -r src/d2c
pip-audit
pytest
rm -rf dist build *.egg-info
python -m build
python -m zipfile -l dist/*.whl
python -m tarfile -l dist/*.tar.gz
twine check dist/*
```

Optional:

```bash
twine upload --repository testpypi dist/*
```

Do not require publishing in this phase.

## Package Metadata Audit

Verify:

- package name
- description
- Python version requirement
- license
- authors
- dependencies
- optional dev dependencies
- URLs if desired
- package data includes runtime skills
- package data excludes caches/secrets

## Artifact Verification

Build:

```bash
rm -rf dist build *.egg-info
python -m build
```

Inspect:

```bash
python -m zipfile -l dist/*.whl
python -m tarfile -l dist/*.tar.gz
twine check dist/*
```

Install artifact into fresh venv:

```bash
python -m venv /tmp/d2c-release-venv
source /tmp/d2c-release-venv/bin/activate
pip install dist/*.whl
python -m d2c --version
python -m d2c --doctor
```

## Optional Release Workflow

If adding GitHub Actions release workflow:

```yaml
on:
  push:
    tags:
      - "v*"
```

Jobs:

- checkout
- setup Python
- install build tools
- run tests/quality gates
- build wheel/sdist
- upload artifacts
- optionally publish to TestPyPI/PyPI

Prefer artifact upload first. Actual PyPI publish can be a later explicit decision.

## Tests

Add tests for:

1. `--version` exits before normal agent startup.
2. CLI output contains `d2c` and the version.
3. `d2c.__version__` is non-empty and valid-ish semver.
4. package metadata version matches `d2c.__version__` if using dynamic/static sync test.

## Verification

Run:

```bash
python -m d2c --version
python -m d2c --doctor
ruff check .
ruff format --check .
mypy src/d2c
bandit -c pyproject.toml -r src/d2c
pip-audit
pytest
python -m build
twine check dist/*
```

If `twine` is not installed, add it to dev dependencies.

## Acceptance Criteria

- `d2c.__version__` exists.
- `python -m d2c --version` works without config/API keys.
- Package metadata version is consistent with CLI version.
- `CHANGELOG.md` exists.
- Release checklist exists.
- Wheel/sdist build and pass `twine check`.
- Runtime package data is present in built artifacts.
- README mentions `--version` or release docs.
- Full Phase 45/46/47 gate suite remains green.

## Expected Outcome

`d2c` can be versioned, built, checked, and released through a repeatable process. This makes the
project ready for external review or a first packaged release without relying on ad hoc local steps.
