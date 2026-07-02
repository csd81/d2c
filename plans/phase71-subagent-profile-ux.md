# Phase 71: Subagent profile UX

**Priority:** MEDIUM-HIGH (make Phase 61 profiles discoverable and debuggable)

## Context

Phase 61 added trusted YAML subagent capability profiles: model, permission mode,
tool boundaries, optional worktree isolation, and profile-specific instructions.
The mechanism is useful, but users need to know which profiles are available,
why a profile is skipped, and what boundaries will apply before they delegate
work to it.

Today, profile behavior is mostly implicit. Invalid or untrusted profiles are
handled by the loader, but there is no simple REPL command to inspect the active
profile set.

## Goal

Add lightweight UX for subagent profiles:

1. List loaded profiles.
2. Show details for a named profile.
3. Explain skipped/invalid/untrusted profiles where possible.
4. Surface effective tool and permission boundaries without exposing secrets.

## Scope

In scope:

- REPL slash commands for profile inspection
- optional headless CLI flag if it fits cleanly
- loader diagnostics surfaced in a user-readable shape
- tests for command behavior and formatting
- README/CLAUDE/docs update

Out of scope:

- editing profiles from the REPL
- creating profile templates
- remote subagents
- swarm/team orchestration
- changing profile schema semantics
- bypassing workspace trust rules

## Proposed UX

Add a `/profiles` REPL command.

Suggested forms:

```text
/profiles
/profiles show <name>
/profiles doctor
```

Behavior:

```text
/profiles
  explore        model: default      mode: default      tools: 8 allowed
  planner        model: reasoner     mode: plan         tools: read-only
  refactor       model: v4-pro       mode: acceptEdits  tools: bounded
```

```text
/profiles show refactor
  name: refactor
  model: deepseek-v4-pro
  permission mode: acceptEdits
  worktree isolation: enabled
  allowed tools: Read, ReadRange, Glob, Grep, Edit, ApplyPatch, GitDiff
  denied tools: Bash
  instructions: 612 chars
```

```text
/profiles doctor
  loaded: 3
  skipped: 1
  skipped profiles:
    unsafe-admin: project profile skipped because workspace is untrusted
```

Default `/profiles` must be read-only and non-destructive.

## Design Notes

Prefer reusing the Phase 61 loader. If it currently discards diagnostics, add a
small structured result:

```python
@dataclass
class ProfileLoadDiagnostic:
    path: Path
    name: str | None
    status: Literal["loaded", "skipped", "invalid"]
    reason: str
```

Avoid secrets:

- Do not print environment variables.
- Do not print full instruction bodies by default.
- Do not print project-local paths beyond profile file paths unless already
  visible in existing diagnostics.

For `show`, summarize instructions by character count and maybe the first
heading only. Full prompt display can be a later explicit flag if needed.

## Files to Inspect / Modify

Likely:

```text
src/d2c/subagent_profiles.py
src/d2c/subagent.py
src/d2c/tools/agent_tool.py
src/d2c/main.py
tests/test_phase61_profiles.py
tests/test_repl_commands.py
```

Optional:

```text
tests/test_phase71_profiles_ux.py
README.md
CLAUDE.md
CHANGELOG.md
docs/security.md
```

## Tests

Add tests for:

1. `/profiles` lists loaded profiles.
2. `/profiles show <name>` prints model, mode, worktree flag, and tool boundary
   summary.
3. `/profiles show missing` reports not found and mutates nothing.
4. `/profiles doctor` reports skipped/invalid profile diagnostics.
5. Untrusted workspace does not load project-local privileged profiles.
6. Output does not include secret-looking values or full instruction bodies.

Use temporary profile directories and trust fixtures. Do not depend on user
home configuration.

## Verification

Fast:

```bash
./scripts/check_fast.sh
python -m pytest tests/test_phase61_profiles.py tests/test_repl_commands.py
```

Before push:

```bash
./scripts/check_fast.sh
python -m pytest tests/test_phase71_profiles_ux.py
```

Do not run the full release gate unless explicitly requested.

## Acceptance Criteria

- Users can list available subagent profiles from the REPL.
- Users can inspect a named profile's effective boundaries.
- Users can see why profiles were skipped or invalid.
- Trust boundaries remain enforced.
- Outputs avoid secrets and full prompt dumps.
- Relevant tests and docs are updated.

## Expected Outcome

Subagent profiles become observable instead of hidden configuration. Users can
understand what an agent profile will do before launching it, and invalid or
trust-blocked profiles become easier to fix.
