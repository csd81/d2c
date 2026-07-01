# Phase 42: Release hardening and fresh-install audit

**Priority:** HIGH (Make the project usable by someone else)

## Context

Phases 34-41 closed the major runtime gaps, hardened safety behavior, added real WebSearch, wired
hooks, and expanded tool breadth from 17 to 23 built-ins. The full suite is green.

The highest-ROI next step is not another feature. It is proving the project can be installed,
configured, tested, and run from a fresh checkout without hidden local assumptions.

## Goal

Perform a release-readiness pass:

1. Verify fresh install from a clean virtual environment.
2. Verify CLI entry points and common commands.
3. Verify config/env failure modes are clear.
4. Verify package data and docs are accurate.
5. Verify no secrets, caches, or local artifacts are tracked.

## Scope

In scope:

- fresh clone / fresh venv install test
- package metadata and package-data audit
- CLI smoke tests
- env/config validation behavior
- README and COMPARISON accuracy
- secret and artifact hygiene
- small fixes discovered during the audit

Out of scope:

- new tools
- new providers
- new permission modes
- UI/TUI redesign
- publishing to PyPI unless explicitly requested later

## Work Plan

### 1. Repository Hygiene

Run:

```bash
git status --short
git ls-files | rg '(__pycache__|\.pyc$|\.env$|secret|token|key)'
```

Verify:

- no `__pycache__` or `.pyc` tracked
- no `.env` tracked
- no API keys or secrets in docs/tests/fixtures
- generated files are either ignored or intentionally tracked

If needed, update `.gitignore`.

### 2. Fresh Virtualenv Install

From outside the current environment:

```bash
python -m venv /tmp/d2c-fresh-venv
source /tmp/d2c-fresh-venv/bin/activate
pip install -U pip
pip install -e ".[dev]"
```

Verify:

```bash
python -m d2c --help
python -m d2c --list-models
pytest
```

Expected:

- install succeeds
- CLI imports without missing package data
- test suite passes

### 3. Package Data Audit

Check `pyproject.toml` and package contents.

Verify package includes anything needed at runtime:

- `src/d2c/skills/commit.md`
- plugin/skill package data if applicable
- no accidental inclusion of plans unless intended

Run:

```bash
python -m build
```

If `build` is not installed:

```bash
pip install build
python -m build
```

Inspect wheel contents:

```bash
python -m zipfile -l dist/*.whl
```

Acceptance:

- runtime files are present
- docs/plans are included only if intentional
- wheel does not include caches/secrets

### 4. CLI Smoke Tests

Verify these commands:

```bash
python -m d2c --help
python -m d2c --list-models
python -m d2c --mcp
python -m d2c --cwd . --max-turns 1 "say hello"
```

For commands requiring API keys, verify both:

- missing-key behavior is clear
- configured behavior works if local keys are available

For MCP server mode, a full interactive JSON-RPC session is optional, but startup/import should be
covered by tests or a short process smoke.

### 5. Config and Env Failure Modes

Test these cases:

```bash
unset DEEPSEEK_API_KEY
python -m d2c "hello"
```

Expected:

- clear message explaining `DEEPSEEK_API_KEY` is required
- no stack trace for normal missing config

WebSearch cases:

```bash
unset D2C_WEBSEARCH_PROVIDER
unset D2C_WEBSEARCH_API_KEY
```

Expected:

- `WebSearch` returns clear "not configured" message

Bad Tavily key:

```bash
D2C_WEBSEARCH_PROVIDER=tavily D2C_WEBSEARCH_API_KEY=bad python -m d2c ...
```

Expected:

- clean auth error
- key not leaked

Sandbox:

```bash
D2C_SANDBOX=1 pytest tests/test_sandbox.py
```

Expected:

- sandbox path remains green

### 6. Session and File-history Smoke

Verify session commands:

```bash
python -m d2c --cwd . "short prompt"
python -m d2c --resume <session_id>
python -m d2c --fork <session_id>
python -m d2c --rewind-files <session_id>
```

If direct manual verification is inconvenient, add a CLI-level test around the session manager and
file-history tracker.

### 7. Docs Accuracy Pass

Update docs only after verifying behavior.

Check:

- README install steps
- required Python version
- env vars
- WebSearch Tavily config
- sandbox config
- CLI flags
- REPL slash commands
- tool count
- known limitations
- COMPARISON open/deferred items

Do not claim a command works unless it was tested or covered by tests.

### 8. Optional CI Workflow

If the repo does not already have CI, add a minimal GitHub Actions workflow:

```yaml
name: tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -e ".[dev]"
      - run: pytest
```

Keep this optional if the project intentionally avoids CI metadata.

## Tests to Run

Minimum:

```bash
pytest
```

Focused:

```bash
pytest tests/test_web_search.py
pytest tests/test_phase37.py
pytest tests/test_phase38.py
pytest tests/test_phase40_hooks.py
pytest tests/test_phase41_tools.py
pytest tests/test_repl_commands.py
pytest tests/test_mcp_server.py
```

Packaging:

```bash
python -m build
python -m zipfile -l dist/*.whl
```

## Acceptance Criteria

- Fresh venv install succeeds.
- Full test suite passes from the fresh venv.
- CLI `--help` and `--list-models` work.
- Missing config produces clear errors, not raw tracebacks.
- WebSearch unconfigured/bad-key paths do not leak secrets.
- Wheel/sdist contain needed runtime files and no caches/secrets.
- README and COMPARISON match verified behavior.
- `git status --short` contains only intentional changes.

## Expected Outcome

The project becomes ready for a new user or reviewer to clone, install, test, and run. This phase
converts the recent implementation depth into release confidence.
