# Phase 24: Background Subagents

**Paper Reference:** Section 8 — "run_in_background field is omitted when background
tasks are disabled"

**Priority:** LOW

## Rationale

Background subagents enable fire-and-forget task delegation. The paper describes them
in the Agent tool schema as a field gated by feature flags. The model can launch a
subagent and continue working, checking results later.

## Files to Create/Modify

1. MODIFY `src/d2c/subagent.py` — add background execution
2. MODIFY `src/d2c/tools/agent_tool.py` — wire background flag

## Key Design

```python
class BackgroundSubagentManager:
    """Manages background subagent execution and notification."""

    def __init__(self):
        self._running: dict[str, asyncio.Task] = {}

    async def launch_background(self, definition, task_prompt, ...):
        """Launch a subagent in the background. Returns immediately with a handle."""
        subagent_id = str(uuid4())[:8]
        task = asyncio.create_task(
            spawn_subagent(definition, task_prompt, ...)
        )
        self._running[subagent_id] = task
        return subagent_id

    def get_status(self, subagent_id: str) -> str:
        """Check status: running, completed, failed."""
        ...

    def get_result(self, subagent_id: str) -> SubagentResult | None:
        ...
```

## AgentTool Integration

```python
async def execute(self, prompt, ..., run_in_background=False):
    if run_in_background:
        bg_id = await bg_manager.launch_background(definition, prompt, ...)
        return ToolResult(
            output=f"Subagent launched in background. ID: {bg_id}\n"
                   f"Use 'check background {bg_id}' for status.",
            metadata={"background": True, "subagent_id": bg_id},
        )
    else:
        result = await spawn_subagent(definition, prompt, ...)
        return ToolResult(output=result.summary, ...)
```

## Edge Cases

- Background subagent fails silently → error logged, result shows failure
- Multiple background agents running → each tracked independently
- Parent session ends with running background → cancel or warn
- Background agent results → model can query status and retrieve

## Tests (~5)

- Background subagent returns immediately
- Status check on running agent
- Status check on completed agent
- Background agent failure captured
- Multiple concurrent background agents
