# Phase 61: Subagent capability profiles

**Priority:** MEDIUM (Delegation fidelity and safety)

## Context

`d2c` supports subagents, but fewer types and less configuration than Claude Code. External analyses
emphasize subagent tool boundaries, model selection, worktree isolation, and specialized profiles.

## Goal

Add richer subagent profiles:

- named agent definitions
- tool allowlists/denylists
- permission mode per profile
- model selection per profile
- optional worktree isolation
- profile-specific instructions

## Scope

In scope:

- profile schema
- loader from trusted config
- AgentTool integration
- tests

Out of scope:

- swarm/team orchestration
- KAIROS heartbeat
- remote subagents
- plugin subagents inheriting privileged fields without trust

## Profile Example

```yaml
name: security-reviewer
model: deepseek-reasoner
permission_mode: plan
tools:
  allow: [Read, Grep, Glob, GitDiff]
  deny: [Write, Edit, Bash]
isolation: worktree
instructions: |
  Review for security vulnerabilities.
```

## Tests

Add tests for:

- profile parsing
- trusted vs untrusted loading
- allow/deny tool boundaries
- permission mode applied
- worktree isolation flag passed
- invalid profile errors

## Acceptance Criteria

- Subagent profiles are loaded only from trusted sources.
- Tool boundaries are enforced.
- Profile model/mode/instructions affect spawned subagent.
- Full gate suite remains green.

