# Phase 62: OS-level sandbox backend

**Priority:** MEDIUM-HIGH (Real isolation depth)

## Context

`d2c` has sandbox wiring, but the current process backend is policy-level restriction, not a strong
filesystem/network jail. Claude Code documents OS-level sandboxing on supported platforms.

## Goal

Add a real OS-level sandbox backend for at least Linux:

- bubblewrap backend if available
- explicit fallback behavior
- clear docs about guarantees and limitations

## Scope

In scope:

- Linux bubblewrap backend
- filesystem allowlist
- network toggle where supported
- child process containment
- tests with feature detection
- doctor check

Out of scope:

- macOS Seatbelt unless explicitly available/testable
- native Windows Sandbox implementation
- container orchestration platform

## Design

Backend:

```text
D2C_SANDBOX_BACKEND=bubblewrap
```

Behavior:

- if requested and unavailable: fail closed or warn depending config
- default remains existing behavior unless explicitly enabled
- allowed dirs include cwd and temp as needed
- network disabled unless configured

## Tests

Add tests for:

- command construction
- unavailable bubblewrap handling
- cwd confined when bubblewrap exists
- network flag mapping
- doctor reports backend status

Use skip-if-unavailable for live bubblewrap tests.

## Acceptance Criteria

- Linux bubblewrap backend is implemented or clearly skipped when unavailable.
- Docs distinguish process backend from OS-level backend.
- Doctor reports backend availability.
- Full gate suite remains green.
