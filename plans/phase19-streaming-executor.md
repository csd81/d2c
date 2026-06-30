# Phase 19: Streaming Tool Executor

**Paper Reference:** Section 4.2 — "begins executing tools as they stream in from the
model response, reducing latency for multi-tool responses."

**Priority:** LOW

## Rationale

Currently tools only execute after the full model response is received. The
StreamingToolExecutor reduces latency by starting tool execution as tool_use blocks
arrive in the stream, before the model finishes generating. The paper describes two
coordination mechanisms: sibling abort controller and progress-available signal.

## Files to Create/Modify

1. CREATE `src/d2c/streaming_executor.py`
2. MODIFY `src/d2c/loop.py` — use StreamingToolExecutor when streaming is enabled

## Key Design

```python
class StreamingToolExecutor:
    """
    Paper: "manages concurrent execution with two coordination mechanisms:
    - Sibling abort controller: Fires when any Bash tool errors, immediately
      terminating other in-flight subprocesses.
    - Progress-available signal: Wakes up getRemainingResults() consumer
      when new output is ready."
    """

    def __init__(self, tools_map, permission_engine, hooks):
        self._pending: dict[str, asyncio.Task] = {}
        self._results: dict[str, ToolResult] = {}
        self._abort = asyncio.Event()
        self._progress = asyncio.Event()
        self._order: list[str] = []

    async def submit(self, tool_use: ToolUse):
        """Submit a tool for execution as soon as it's parsed from the stream."""
        self._order.append(tool_use.id)
        task = asyncio.create_task(self._execute(tool_use))
        self._pending[tool_use.id] = task

    async def get_results(self) -> list[tuple[ToolUse, ToolResult]]:
        """Wait for all submitted tools, return in original order."""
        while len(self._results) < len(self._order):
            await self._progress.wait()
            self._progress.clear()
        return [(self._order[i], self._results[tid])
                for i, tid in enumerate(self._order)]

    def abort_all(self):
        """Sibling abort: terminate all in-flight tools."""
        self._abort.set()
        for task in self._pending.values():
            task.cancel()
```

## Integration in loop.py

```python
if loop_config.stream:
    executor = StreamingToolExecutor(tools_map, ...)
    async with client.messages.stream(...) as stream:
        async for event in stream:
            if event.type == "content_block_start":
                if event.content_block.type == "tool_use":
                    await executor.submit(ToolUse(...))
            elif event.type == "text_delta":
                yield TextDelta(text=event.text)

    for tu, result in await executor.get_results():
        yield ToolExecutionEvent(tool_use=tu, result=result)
```

## Edge Cases

- Tool finishes before stream ends → result buffered
- Stream ends without tools → executor idle, no-op
- Sibling abort mid-execution → remaining tools cancelled
- Mixed text + tool_use in stream → text yielded, tools executed

## Tests (~6)

- Tool submitted during stream, result after stream ends
- Sibling abort cancels in-flight tools
- Results returned in original submission order
- Single tool stream → executes during streaming
- Zero tools in stream → executor handles gracefully
