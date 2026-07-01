# Phase 47: Config doctor and diagnostics command

**Priority:** HIGH (User support and production operability)

## Context

`d2c` now has many optional and environment-sensitive systems:

- DeepSeek model/API config
- Tavily WebSearch
- sandboxing
- workspace trust
- MCP
- skills/plugins
- audit logging
- git/worktree support
- package data
- CI quality gates

A new user or operator needs a quick way to answer:

```text
Is this installation configured correctly?
What is missing?
What is intentionally disabled?
What should I fix first?
```

This phase adds a `doctor` diagnostics command.

## Goal

Implement:

```bash
python -m d2c --doctor
```

or:

```bash
python -m d2c doctor
```

The command should run local diagnostics and print actionable `PASS` / `WARN` / `FAIL` results.

## Scope

In scope:

- CLI doctor command
- local environment/config checks
- optional live provider checks behind a flag
- machine-readable output option if low effort
- tests for diagnostic result logic
- README documentation

Out of scope:

- auto-fixing config
- interactive setup wizard
- remote telemetry
- live model calls by default
- live Tavily calls by default
- CI redesign

## Files to Create/Modify

1. CREATE `src/d2c/doctor.py`
   - diagnostic check definitions
   - result model
   - text/JSON rendering

2. MODIFY `src/d2c/main.py`
   - add CLI flag/subcommand
   - run doctor before normal agent startup

3. MODIFY `README.md`
   - document doctor usage

4. CREATE `tests/test_doctor.py`
   - unit tests for checks/rendering

## Command Shape

Recommended CLI:

```bash
python -m d2c --doctor
```

Optional flags:

```bash
python -m d2c --doctor --doctor-live
python -m d2c --doctor --json
```

Defaults:

- no live external API calls
- no secrets printed
- exit code reflects severe failures

## Result Model

Use a small result type:

```python
@dataclass
class DoctorResult:
    name: str
    status: Literal["pass", "warn", "fail"]
    message: str
    fix: str | None = None
```

Exit code:

```text
0 if no FAIL
1 if any FAIL
```

Warnings should not fail the command.

## Checks

### 1. Python version

Check:

```text
Python >= 3.11
```

Status:

- `PASS` if supported
- `FAIL` if too old

### 2. Package imports

Import key modules:

- `d2c`
- `d2c.loop`
- `d2c.tools.pool`
- `d2c.permissions`
- `d2c.observability`

Status:

- `PASS` if imports work
- `FAIL` with import error class only, not noisy traceback

### 3. DeepSeek config

Check:

- `DEEPSEEK_API_KEY` exists
- base URL configured/defaulted
- model configured/defaulted

Status:

- `PASS` if API key present
- `WARN` if missing

Do not print the key.

### 4. WebSearch config

Check:

- `D2C_WEBSEARCH_PROVIDER`
- `D2C_WEBSEARCH_API_KEY` when provider requires it
- supported provider name

Status:

- `PASS` if configured
- `WARN` if unconfigured
- `FAIL` if unsupported provider name

Optional live check:

```bash
python -m d2c --doctor --doctor-live
```

If live enabled:

- make a small Tavily query
- report auth/rate-limit/timeout cleanly
- never print the key

### 5. Git availability

Check:

```bash
git --version
```

Status:

- `PASS` if available
- `WARN` if unavailable

### 6. Current workspace

Check:

- cwd exists
- cwd readable
- git repo status if applicable
- write access if needed for file-history/checkpoints

Status:

- `PASS` if cwd usable
- `WARN` if not a git repo
- `FAIL` if cwd inaccessible

### 7. Workspace trust

Check:

- trust decision for cwd
- whether local `.env`, MCP, plugins, skills, and memory are enabled/skipped

Status:

- `PASS` trusted
- `WARN` untrusted, with explanation that local extensions are skipped

### 8. Sandbox config

Check:

- `D2C_SANDBOX`
- backend availability
- Docker availability if docker backend configured
- Windows sandbox backend stub warning if selected

Status:

- `PASS` disabled/default is acceptable
- `PASS` process backend available when enabled
- `WARN` docker not available when requested
- `WARN` windows-sandbox backend is not fully implemented

### 9. Audit logging

Check:

- `D2C_AUDIT_LOG`
- audit log path parent directory writable if enabled
- prompt/tool-output logging flags

Status:

- `PASS` disabled/default
- `PASS` enabled and writable
- `FAIL` enabled but path not writable
- `WARN` full prompt/tool-output logging enabled

### 10. MCP config

Check:

- MCP config file parseable if present
- local MCP skipped when untrusted

Status:

- `PASS` no config or parseable config
- `WARN` skipped due to trust
- `FAIL` malformed config

### 11. Skills/plugins/package data

Check:

- bundled `skills/commit.md` present
- plugin manifests parse if present/trusted
- local skills skipped when untrusted

Status:

- `PASS` package data present
- `WARN` local skills/plugins skipped due to trust
- `FAIL` bundled runtime data missing

## Output

Human output:

```text
d2c doctor

PASS Python              3.11.8
PASS Imports             core modules import
WARN DeepSeek            DEEPSEEK_API_KEY is not set
PASS WebSearch           provider=tavily
PASS Git                 git version 2.43.0
WARN Trust               workspace is untrusted; local plugins/MCP/skills are skipped
PASS Audit log           disabled

Summary: 5 passed, 2 warnings, 0 failed
```

JSON output:

```json
{
  "summary": {"pass": 5, "warn": 2, "fail": 0},
  "results": [...]
}
```

## Tests

Add tests for:

1. result summary counts
2. text renderer
3. JSON renderer
4. missing DeepSeek key -> WARN, no key leak
5. unsupported WebSearch provider -> FAIL
6. unconfigured WebSearch -> WARN
7. audit log enabled with unwritable path -> FAIL
8. bundled skill missing mocked -> FAIL
9. untrusted workspace reports skipped local extensions
10. CLI `--doctor` exits before normal agent loop

## Verification

Run:

```bash
pytest tests/test_doctor.py
pytest
ruff check .
ruff format --check .
mypy src/d2c
bandit -c pyproject.toml -r src/d2c
pip-audit
python -m build
```

Manual:

```bash
python -m d2c --doctor
python -m d2c --doctor --json
python -m d2c --doctor --doctor-live
```

## Acceptance Criteria

- `python -m d2c --doctor` runs without needing model/API access.
- Missing optional config is reported as `WARN`, not a crash.
- Broken required runtime/package data is reported as `FAIL`.
- No secrets are printed in text or JSON output.
- JSON output is machine-readable.
- Exit code is `1` only when at least one `FAIL` exists.
- README documents the command.
- Full Phase 45/46 gate suite remains green.

## Expected Outcome

Users and operators can diagnose setup problems in one command. This reduces support friction and
makes the project easier to install, run, debug, and review.
