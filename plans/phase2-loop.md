# Phase 2: Agent Loop & Context Assembly

## Files

- `src/d2c/loop.py` — queryLoop() async generator, LoopState, LoopConfig
- `src/d2c/context.py` — getSystemContext(), getUserContext(), assembleMessages()
- `src/d2c/config.py` — Config (expanded)
- `src/d2c/main.py` — basic CLI entry
- `src/d2c/__main__.py` — `python -m d2c`
- `tests/test_loop.py`

## Key Types

- `LoopState`: messages, tool_context, turn_count, output_tokens_recovery_attempts, has_attempted_reactive_compact, stopped, stop_reason
- `LoopConfig`: system_prompt, user_context, permission_callback, model, max_turns, tools, hooks, config, session_store, permission_engine, compact_config
- `LoopEvent`: TextResponse, ToolExecutionEvent, StopEvent (async generator yields)

## Core Flow

```
while not stopped:
    1. Assemble context (system prompt, CLAUDE.md, history, tool schemas)
    2. Call model with tools
    3. If no tool_use → run stop hooks, break
    4. For each tool_use block:
       a. Permission gate (deny-first rules → mode → user prompt)
       b. Run PreToolUse hooks
       c. Execute tool
       d. Run PostToolUse hooks
    5. Append tool results to history
    6. Compact if context pressure exceeds threshold
```

## Key Design Decisions

- Read-only tools run in parallel; writes serialize
- Recovery: max output token escalation (up to 3 retries), prompt_too_long handling
- Stop conditions: no tool use, max turns, hook intervention, context overflow
- Context assembly is memoized (git status, env cached)
- CLAUDE.md delivered as user-context message, not system prompt

## Edge Cases

- Model returns text only → primary stop
- Prompt too long from API → reactive compact, then terminate
- Max output tokens → retry with escalated limit (up to 3x)
- Tool throws exception → catch, convert to error ToolResult
- Hook vetoes stop → inject guidance, continue
- Max turns reached → terminate
