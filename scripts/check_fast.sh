#!/usr/bin/env bash
#
# Fast pre-commit checks (Phase 69) — the inner-loop gate for normal work.
#
# Runs the quick static checks and, if you pass test paths, the targeted tests
# for what you touched. For the full heavy gate (all tests, security, build)
# run scripts/check_release.sh before pushing / completing a phase.
#
#   ./scripts/check_fast.sh                       # lint + format + types
#   ./scripts/check_fast.sh tests/test_eval.py    # + run those tests
#
# Uses `python -m pytest` (not bare `pytest`) so import behavior stays stable.
set -euo pipefail

cd "$(dirname "$0")/.."

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$1"; }

step "Lint (ruff check)"
python -m ruff check .

step "Format check (ruff format --check)"
python -m ruff format --check .

step "Type check (mypy)"
python -m mypy

if [ "$#" -gt 0 ]; then
    step "Tests (targeted): $*"
    python -m pytest "$@"
else
    printf '\n(no test paths given — pass e.g. tests/test_foo.py to run targeted tests)\n'
fi

printf '\n\033[1;32mFast checks passed.\033[0m\n'
