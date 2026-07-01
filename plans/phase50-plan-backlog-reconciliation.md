# Phase 50: Plan backlog reconciliation

**Priority:** HIGH (Strategic cleanup before more feature work)

## Context

The project has grown through many phase plans. Some planned work was implemented, some was replaced
by better designs, some was deferred intentionally, and some may still be stale or partially done.

Before adding more features, Phase 50 should reconcile the historical plans against the current code:

```text
What did we plan?
What is actually implemented?
What is deferred?
What is obsolete?
What is still worth doing?
```

## Goal

Read all previous plans and produce an implementation backlog assessment:

1. Inventory every deferred, partial, or unimplemented item from prior plans.
2. Verify each item against the current source/tests/docs.
3. Classify each item as implemented, partially implemented, deferred, obsolete, or candidate.
4. Rank candidate work by ROI, risk, effort, and paper fidelity.
5. Create a clear next-phase recommendation list.

## Scope

In scope:

- all files under `plans/`
- `COMPARISON.md`
- `README.md`
- `CLAUDE.md`
- `docs/`
- source-level verification in `src/d2c/`
- test-level verification in `tests/`
- backlog table / roadmap output

Out of scope:

- implementing the backlog items during Phase 50
- large refactors
- changing architecture
- rewriting old plans

## Files to Inspect

Plans:

```bash
find plans -type f -name '*.md' | sort
```

Current truth:

```bash
COMPARISON.md
README.md
CLAUDE.md
docs/
src/d2c/
tests/
pyproject.toml
.github/workflows/
```

## Method

### 1. Plan Inventory

For every plan file, extract:

- phase number/title
- original goal
- files planned
- explicit out-of-scope items
- deferred items
- acceptance criteria
- TODOs / caveats / limitations
- items marked optional

Create a raw inventory table:

```text
Phase | Item | Type | Source line | Current status | Evidence | Notes
```

Types:

```text
planned
deferred
optional
out_of_scope
caveat
known_limitation
acceptance_gap
```

### 2. Current Code Verification

For each item, verify by source/test evidence.

Statuses:

```text
implemented
tested
partially_implemented
implemented_but_untested
deferred
obsolete
not_implemented
unknown
```

Evidence examples:

```text
source: src/d2c/tools/web_search.py
tests: tests/test_web_search.py
docs: README.md
commit: Phase 39
```

Do not mark an item implemented only because a plan says it is. Verify source or tests.

### 3. Classification

Classify remaining items:

#### Keep

Still valuable and feasible.

#### Defer

Valid, but lower ROI or blocked by bigger design decisions.

#### Drop

Obsolete, replaced by a better implementation, or outside project goals.

#### Research

Needs clarification before implementation.

### 4. ROI Scoring

Score candidate items:

```text
User value:       1-5
Safety impact:   1-5
Paper fidelity:  1-5
Implementation effort: 1-5
Risk:            1-5
Testability:     1-5
```

Suggested priority formula:

```text
priority = user_value + safety_impact + paper_fidelity + testability - effort - risk
```

Do not overfit the formula. Use engineering judgment.

## Expected Remaining Areas

Likely candidates to evaluate:

1. `bubble` permission mode
2. KAIROS background heartbeat mode
3. native Windows sandbox backend
4. SearXNG/Brave/Google secondary WebSearch providers
5. remaining tool breadth toward paper's ~54 tools
6. richer plugin/skill ecosystem
7. full interactive permission UI / persistent approvals if Phase 49 leaves follow-ups
8. true browser/computer-use tools
9. OpenTelemetry/exporter support after local observability
10. multi-platform CI matrix
11. release publishing to TestPyPI/PyPI
12. stricter full-repo typing
13. Docker image / packaged binary distribution
14. config wizard / setup assistant
15. deeper prompt-injection defenses for web/memory content

This list is only a starting hypothesis. The real list must come from reading the plans.

## Deliverables

Create:

```text
plans/backlog-reconciliation.md
```

It should include:

1. Executive summary
2. Implemented/resolved items
3. Remaining candidates
4. Deferred intentionally
5. Obsolete/dropped items
6. Highest-ROI next phases
7. Evidence links to source/tests/docs

Recommended structure:

```markdown
# Backlog Reconciliation

## Summary

## Method

## Implemented

| Item | Evidence |

## Still Open

| Item | Status | ROI | Effort | Risk | Recommendation | Evidence |

## Deferred

| Item | Reason |

## Obsolete / Dropped

| Item | Reason |

## Recommended Next Phases

1. Phase 51: ...
2. Phase 52: ...
3. Phase 53: ...
```

## Tests / Verification

Run the existing gates to ensure the reconciliation does not accidentally change runtime behavior:

```bash
ruff check .
ruff format --check .
mypy src/d2c
bandit -c pyproject.toml -r src/d2c
pip-audit
pytest
python -m build
twine check dist/*
```

If Phase 50 only adds markdown, at minimum run:

```bash
git diff --check
```

## Acceptance Criteria

- Every plan file was reviewed.
- Every deferred/out-of-scope/optional item was classified.
- Remaining candidates are verified against current code, not assumed.
- Recommendations are ranked by ROI and implementation risk.
- `plans/backlog-reconciliation.md` exists.
- No runtime code changes are made unless strictly needed for documentation tooling.

## Expected Outcome

The project gets a clean strategic backlog. Future phases are chosen from verified gaps rather than
memory, stale plans, or duplicated work.
