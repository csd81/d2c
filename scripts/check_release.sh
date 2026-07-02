#!/usr/bin/env bash
#
# Full release gate (Phase 69) — run before pushing, releasing, or completing
# a phase. This is the comprehensive suite; for the inner loop use
# scripts/check_fast.sh instead.
#
#   ./scripts/check_release.sh
#
# It is a superset of check_fast: static checks (lint/format/types) first, then
# the heavy checks (full test suite, security lint, dependency audit, a clean
# package build, and artifact validation). There is no automatic GitHub CI —
# this is the gate.
#
# Notes:
#   - Uses `python -m pytest` (not bare `pytest`) for stable import behavior.
#   - `pip-audit` is advisory (upstream CVEs don't block the gate); review it.
#   - `dist/` is cleared before building so `twine check` can't pass or fail on
#     stale artifacts. `dist/`/`build/` are gitignored — no tracked files change.
#   - No live model/API credentials are required.
set -euo pipefail

cd "$(dirname "$0")/.."

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$1"; }

# Static checks (lint / format / types) — reuse the fast gate, no test args.
./scripts/check_fast.sh

step "Tests (full suite)"
python -m pytest

step "Security lint (bandit)"
python -m bandit -c pyproject.toml -r src/d2c

step "Dependency audit (pip-audit, advisory)"
python -m pip_audit || echo "pip-audit reported findings (advisory — review above)"

step "Build package (clean dist first)"
rm -rf dist
python -m build

step "Validate artifacts (twine check)"
python -m twine check dist/*

printf '\n\033[1;32mRelease gate passed.\033[0m\n'
