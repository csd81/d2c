# Phase 15: Remaining 18 Hook Events

**Paper Reference:** Section 6.1 — 27 hook events total (we have 9)

**Priority:** MEDIUM

## Rationale

The 18 missing events cover session lifecycle, subagent lifecycle, notifications,
configuration changes, and elicitation. They enable deeper extensibility and audit.
Currently implemented: SessionStart, UserPromptSubmit, PreToolUse, PostToolUse,
PostToolUseFailure, PermissionDenied, Stop, PreCompact, SubagentStop.

## Files to Modify

1. MODIFY `src/d2c/hooks.py` — add 18 new HookEvent values and wire them

## New Events

| Event | When Fired |
|---|---|
| `SessionEnd` | Session is ending |
| `Setup` | After initialization, before first prompt |
| `StopFailure` | Stop hook itself fails |
| `Elicitation` | Model requests clarification from user |
| `ElicitationResult` | User responds to elicitation |
| `SubagentStart` | Subagent begins execution |
| `TeammateIdle` | Coordinated agent team member idle |
| `TaskCreated` | New task created in task tracking |
| `TaskCompleted` | Task marked complete |
| `PostCompact` | After compaction completes |
| `InstructionsLoaded` | CLAUDE.md loaded |
| `ConfigChange` | Config modified during session |
| `CwdChanged` | Working directory changes |
| `FileChanged` | File modified on disk (external) |
| `WorktreeCreate` | Git worktree created for subagent |
| `WorktreeRemove` | Git worktree removed |
| `PermissionRequest` | Before permission dialog shown |
| `Notification` | Generic notification event |

## Wiring Locations

```python
# SessionEnd — fire in main.py before exit
await hooks.fire(HookEvent.SESSION_END, {"session_id": ..., "turns": ...})

# SubagentStart — fire in subagent.py spawn_subagent()
await parent_hooks.fire(HookEvent.SUBAGENT_START, {
    "subagent_type": definition.subagent_type,
    "task_prompt": task_prompt,
})

# PostCompact — after autoCompact() returns
await hooks.fire(HookEvent.POST_COMPACT, {
    "pre_count": len(original),
    "post_count": len(post_compact),
})
```

## Tests (~10)

- SessionEnd fires on session exit
- SubagentStart fires when agent spawned
- Each event's context schema is correct
- Hook errors in new events are non-fatal
- Multiple hooks on same event merge correctly
