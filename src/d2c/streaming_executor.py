"""Streaming tool executor. Paper Section 4.2.

Begins executing tools as they stream in from the model response, reducing
latency for multi-tool responses. Tools that complete during streaming are
started immediately in background tasks rather than waiting for the full
response.

Coordination mechanisms:
- Sibling abort controller: Fires when any Bash tool errors, immediately
  terminating other in-flight subprocesses.
- Progress-available signal: Wakes up get_results() consumer when new
  output is ready.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from d2c.tools import Tool, ToolResult, ToolUse

logger = logging.getLogger(__name__)


class StreamingToolExecutor:
    """Manages concurrent tool execution during streaming model responses.

    Paper: "manages concurrent execution with two coordination mechanisms:
    - Sibling abort controller: Fires when any Bash tool errors, immediately
      terminating other in-flight subprocesses.
    - Progress-available signal: Wakes up getRemainingResults() consumer
      when new output is ready."
    """

    def __init__(
        self,
        tools_map: dict[str, Tool],
        permission_engine: Any = None,
        hooks: Any = None,
        session_store: Any = None,
        approval_callback: Any = None,
    ):
        self._tools_map = tools_map
        self._permission_engine = permission_engine
        self._hooks = hooks
        self._session_store = session_store
        self._approval_callback = approval_callback

        self._pending: dict[str, asyncio.Task[tuple[ToolUse, ToolResult]]] = {}
        self._results: dict[str, tuple[ToolUse, ToolResult]] = {}
        self._abort = asyncio.Event()
        self._progress = asyncio.Event()
        self._order: list[str] = []
        self._lock = asyncio.Lock()

    def submit(self, tool_use: ToolUse) -> None:
        """Submit a tool for execution as soon as it's parsed from the stream.

        Paper: "tools begin executing as their tool_use blocks arrive in
        the stream, before the model finishes generating."
        """
        self._order.append(tool_use.id)
        task = asyncio.create_task(
            self._execute_and_store(tool_use),
            name=f"stream-exec-{tool_use.name}-{tool_use.id[:8]}",
        )
        self._pending[tool_use.id] = task

    def is_already_submitted(self, tool_use_id: str) -> bool:
        """Check if a tool_use id was already submitted during streaming."""
        return tool_use_id in self._order

    async def get_results(self) -> list[tuple[ToolUse, ToolResult]]:
        """Wait for all submitted tools, return in original submission order.

        Paper: "output order stays the same even when tools run in parallel."
        """
        while len(self._results) < len(self._order):
            await self._progress.wait()
            self._progress.clear()

        return [self._results[tid] for tid in self._order if tid in self._results]

    def abort_all(self) -> None:
        """Sibling abort: terminate all in-flight tools.

        Paper: "Fires when any Bash tool errors, immediately terminating
        other in-flight subprocesses."
        """
        self._abort.set()
        for task in self._pending.values():
            task.cancel()

    def has_pending(self) -> bool:
        """Check if there are tools still executing."""
        return any(not t.done() for t in self._pending.values())

    async def _execute_and_store(
        self,
        tool_use: ToolUse,
    ) -> tuple[ToolUse, ToolResult]:
        """Execute a single tool and store the result."""
        try:
            result = await self._execute_one(tool_use)
        except asyncio.CancelledError:
            result = ToolResult(
                output="Aborted: sibling tool error.",
                error=True,
                metadata={"aborted": True},
            )

        async with self._lock:
            self._results[tool_use.id] = (tool_use, result)
            self._progress.set()

        # Sibling abort: if Bash tool errors, cancel in-flight tools
        if tool_use.name == "Bash" and result.error:
            logger.info(
                "Bash tool '%s' errored — triggering sibling abort",
                tool_use.id[:8],
            )
            self.abort_all()

        return tool_use, result

    async def _execute_one(self, tool_use: ToolUse) -> ToolResult:
        """Execute a single tool with permission gating and hooks."""
        from d2c.permissions import PermissionDecision, PermissionRequest

        if self._abort.is_set():
            return ToolResult(
                output="Aborted: sibling tool error.",
                error=True,
                metadata={"aborted": True},
            )

        tool = self._tools_map.get(tool_use.name)
        if not tool:
            return ToolResult(
                output=f"Error: unknown tool '{tool_use.name}'",
                error=True,
                metadata={"unknown_tool": True},
            )

        import time as _time

        from d2c.observability import audit, logs_tool_outputs

        _t0 = _time.perf_counter()
        audit(
            "tool_call_start",
            tool_name=tool_use.name,
            tool_call_id=tool_use.id,
            category=getattr(tool.category, "value", None),
            streaming=True,
        )

        # PreToolUse hook
        if self._hooks:
            from d2c.hooks import HookEvent

            pre_result = await self._hooks.fire(
                HookEvent.PRE_TOOL_USE,
                {
                    "tool_name": tool_use.name,
                    "tool_input": tool_use.input,
                },
            )
            if pre_result.decision == "deny":
                return ToolResult(
                    output=f"Hook denied: {pre_result.error or 'PreToolUse hook denied'}",
                    error=True,
                    metadata={"hook_denied": True},
                )
            if pre_result.updated_input:
                tool_use.input = pre_result.updated_input

        # Permission gate
        if self._permission_engine:
            perm_request = PermissionRequest(
                tool_name=tool_use.name,
                tool_input=tool_use.input,
                tool_category=tool.category,
            )
            # Phase 38: fail CLOSED — a permission-evaluation error must not
            # execute the tool.
            try:
                perm_result = await self._permission_engine.evaluate_async(perm_request)
            except Exception as e:
                if self._hooks:
                    from d2c.hooks import HookEvent

                    await self._hooks.fire(
                        HookEvent.PERMISSION_DENIED,
                        {
                            "tool_name": tool_use.name,
                            "reason": f"permission evaluation error: {type(e).__name__}",
                        },
                    )
                return ToolResult(
                    output=f"Permission check failed ({type(e).__name__}); denying for safety.",
                    error=True,
                    metadata={"denied": True, "permission_error": True},
                )

            # Phase 43/49: resolve ASK — do not execute speculatively.
            from d2c.permissions import (
                PERMISSION_REQUIRED_REASON,
                classify_permission_event,
                resolve_permission_decision,
            )

            raw_result = perm_result
            if raw_result is not None and raw_result.decision == PermissionDecision.ASK:
                audit(
                    "permission_ask",
                    tool_name=tool_use.name,
                    tool_call_id=tool_use.id,
                    reason=raw_result.reason,
                )
            perm_result = await resolve_permission_decision(
                perm_request,
                raw_result,
                self._approval_callback,
            )
            event = classify_permission_event(raw_result, perm_result)
            if event:
                audit(
                    event,
                    level="INFO" if event == "permission_approved" else "WARNING",
                    tool_name=tool_use.name,
                    tool_call_id=tool_use.id,
                    reason=perm_result.reason if perm_result else None,
                )

            if perm_result is not None and perm_result.decision != PermissionDecision.ALLOW:
                result = ToolResult(
                    output=f"Permission denied: {perm_result.reason}",
                    error=True,
                    metadata={
                        "denied": True,
                        "permission_required": perm_result.reason == PERMISSION_REQUIRED_REASON,
                    },
                )
                if self._hooks:
                    from d2c.hooks import HookEvent

                    await self._hooks.fire(
                        HookEvent.PERMISSION_DENIED,
                        {
                            "tool_name": tool_use.name,
                            "reason": perm_result.reason,
                        },
                    )
                return result

        try:
            result = await tool.execute(**tool_use.input)

            # PostToolUse hook
            if self._hooks:
                from d2c.hooks import HookEvent

                post_result = await self._hooks.fire(
                    HookEvent.POST_TOOL_USE,
                    {
                        "tool_name": tool_use.name,
                        "tool_input": tool_use.input,
                        "tool_result": result.output,
                        "error": result.error,
                    },
                )
                if post_result.updated_output:
                    result.output = post_result.updated_output
                if post_result.additional_context:
                    result.output += f"\n[Hook context: {post_result.additional_context}]"

            audit(
                "tool_call_end",
                tool_name=tool_use.name,
                tool_call_id=tool_use.id,
                streaming=True,
                duration_ms=round((_time.perf_counter() - _t0) * 1000, 1),
                status="error" if result.error else "ok",
                error=result.error,
                output_len=len(result.output),
                output=(result.output if logs_tool_outputs() else None),
            )
            return result
        except Exception as e:
            audit(
                "tool_call_error",
                level="ERROR",
                tool_name=tool_use.name,
                tool_call_id=tool_use.id,
                streaming=True,
                duration_ms=round((_time.perf_counter() - _t0) * 1000, 1),
                error=True,
                error_class=type(e).__name__,
            )
            error_result = ToolResult(
                output=f"Error executing tool '{tool_use.name}': {e}",
                error=True,
                metadata={"exception": str(e)},
            )
            if self._hooks:
                from d2c.hooks import HookEvent

                await self._hooks.fire(
                    HookEvent.POST_TOOL_USE_FAILURE,
                    {
                        "tool_name": tool_use.name,
                        "tool_input": tool_use.input,
                        "error": str(e),
                    },
                )
            return error_result


class StreamToolParser:
    """Parses tool_use blocks from streaming events.

    Accumulates partial JSON input from input_json_delta events, producing
    complete ToolUse objects when a tool_use content block finishes.
    """

    def __init__(self):
        self._pending: dict[int, dict[str, Any]] = {}
        self._completed: list[ToolUse] = []

    def feed_start(self, index: int, name: str, block_id: str) -> None:
        """Begin tracking a new tool_use content block."""
        self._pending[index] = {
            "id": block_id,
            "name": name,
            "json_chunks": [],
        }

    def feed_delta(self, index: int, partial_json: str) -> None:
        """Accumulate a partial JSON chunk for a tool_use block."""
        if index in self._pending:
            self._pending[index]["json_chunks"].append(partial_json)

    def feed_stop(self, index: int) -> ToolUse | None:
        """Finalize a tool_use block. Returns the ToolUse if JSON parsed OK."""
        if index not in self._pending:
            return None

        info = self._pending.pop(index)
        json_str = "".join(info["json_chunks"]).strip()

        if not json_str:
            tool_input = {}
        else:
            try:
                tool_input = json.loads(json_str)
            except (json.JSONDecodeError, TypeError):
                logger.debug("Failed to parse tool input JSON for block %d", index)
                return None

        tool_use = ToolUse(id=info["id"], name=info["name"], input=tool_input)
        self._completed.append(tool_use)
        return tool_use

    @property
    def completed(self) -> list[ToolUse]:
        return list(self._completed)

    @property
    def submitted_ids(self) -> set[str]:
        return {tu.id for tu in self._completed}
