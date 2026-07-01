# Phase 54: Expand mypy to full src/d2c

**Priority:** MEDIUM (Type coverage maturity)

## Context

Phase 45 introduced staged mypy over clean core modules. The backlog reconciliation identifies full
`src/d2c` typing as a worthwhile incremental hardening step.

## Goal

Expand mypy coverage from the staged subset to all of:

```text
src/d2c
```

Do this incrementally, avoiding large behavioral refactors.

## Scope

In scope:

- type annotations
- small helper types/protocols
- dataclass/TypedDict cleanup
- narrowing `Any` where practical
- mypy config updates
- tests remain unchanged except where needed for typing imports

Out of scope:

- runtime behavior changes
- major architecture changes
- replacing mypy with pyright
- strict mode everywhere if too disruptive

## Method

1. Run current staged mypy.
2. Run `mypy src/d2c` and capture errors.
3. Group errors by module.
4. Fix low-risk modules first.
5. Add narrow ignores only with reasons.
6. Update `[tool.mypy].files` to `src/d2c` once green.

## Prioritization

Order:

1. pure utility modules
2. tools
3. permissions
4. observability/doctor/config
5. loop/streaming executor
6. MCP/subagent/plugin modules

## Rules

- Prefer precise types over broad `Any`.
- Use `Protocol` for callback/tool/provider interfaces.
- Use `TypedDict` for structured dicts only when it improves clarity.
- Do not contort readable code solely for mypy.
- Every `# type: ignore` needs an error code and short reason.

## Files to Modify

- `pyproject.toml`
- `src/d2c/**/*.py`
- optionally `tests/` if test helper types are needed

## Verification

Run:

```bash
mypy src/d2c
pytest
ruff check .
ruff format --check .
bandit -c pyproject.toml -r src/d2c
pip-audit
python -m build
twine check dist/*
```

## Acceptance Criteria

- `mypy src/d2c` passes.
- CI uses full `src/d2c` mypy target.
- No unjustified blanket ignores.
- Runtime behavior remains unchanged.
- Full gate suite remains green.

