# Phase 60: Scoped settings and managed policy

**Priority:** MEDIUM-HIGH (Team governance)

## Context

Claude Code documents layered settings scopes: managed, user, project, and local. `d2c` has config
and a trust gate, but not a full scoped settings model with managed policy precedence.

## Goal

Implement layered settings:

```text
managed > user > project > local > env/defaults
```

with deny-first policy merging and trust-aware project/local loading.

## Scope

In scope:

- settings file discovery
- merge precedence
- managed policy locks
- permission rule layering
- trust-aware loading
- doctor diagnostics
- tests

Out of scope:

- enterprise deployment tooling
- remote policy server
- GUI settings editor

## Proposed Locations

- managed: `/etc/d2c/settings.yaml` or env override
- user: `~/.d2c/settings.yaml`
- project: `.d2c/settings.yaml`
- local: `.d2c/settings.local.yaml` ignored by git

## Policy Rules

- managed deny rules cannot be weakened by lower scopes
- project/local settings load only when trusted
- local settings are not intended for source control
- secrets should stay in env or `.env`, not settings YAML

## Tests

Add tests for:

- precedence order
- managed lock cannot be overridden
- untrusted project skips project/local settings
- deny rules dominate allow rules
- malformed settings error reporting
- doctor output

## Acceptance Criteria

- Scoped settings are documented and tested.
- Managed policy can restrict lower scopes.
- Trust gate still prevents pre-trust local code/config expansion.
- Full gate suite remains green.

