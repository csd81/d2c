# Phase 7: Hooks

## Files

- `src/d2c/hooks.py` — HookEvent, HookType, HookDefinition, HookResult, HookRegistry
- `tests/test_hooks.py`

## 8 Core Events (of paper's 27)

- SessionStart — one-shot context injection
- UserPromptSubmit — inject/block on each user turn
- PreToolUse — approve/block/rewrite tool input
- PostToolUse — mutate output or inject context
- PostToolUseFailure — error-specific guidance
- PermissionDenied — retry guidance after denial
- Stop — veto stop, force loop continuation
- PreCompact — inject instructions before compaction

## Hook Types

- command — shell command (stdin JSON, stdout JSON)
- prompt — LLM-based hook
- http — HTTP webhook
- callback — SDK/internal (not persistable)

## Result Merging

- Any deny → overall deny
- First updated_input wins
- Contexts concatenate
- Any veto → veto=True
- Hook failures are non-fatal
