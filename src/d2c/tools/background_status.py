"""Background subagent status tool. Phase 34.

Exposes the BackgroundSubagentManager to the model so it can check on and
retrieve results from subagents launched via AgentTool(background=True).
Without this tool, background subagents were unreachable (the AgentTool
message referenced status tools that did not exist).
"""

from __future__ import annotations

from typing import Any, ClassVar

from d2c.tools import PermissionCategory, Tool, ToolResult


class BackgroundStatusTool(Tool):
    name: ClassVar[str] = "AgentStatus"
    description: ClassVar[str] = (
        "Check the status of background subagents launched with the Agent tool. "
        "Call with no arguments to list all background subagents and their statuses. "
        "Pass a subagent_id to get that subagent's status and, if finished, its result."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "subagent_id": {
                "type": "string",
                "description": "The background subagent id to query. Omit to list all.",
            },
        },
        "required": [],
    }
    category: ClassVar[PermissionCategory] = PermissionCategory.READ
    is_concurrent_safe: ClassVar[bool] = True

    async def execute(self, subagent_id: str | None = None, **kwargs: Any) -> ToolResult:
        from d2c.subagent import get_background_manager

        manager = get_background_manager()

        if not subagent_id:
            statuses = manager.statuses()
            if not statuses:
                return ToolResult(output="No background subagents.")
            lines = [f"  {sid}: {status}" for sid, status in statuses.items()]
            return ToolResult(
                output="Background subagents:\n" + "\n".join(lines),
                metadata={"statuses": statuses},
            )

        status = manager.get_status(subagent_id)
        if status == "unknown":
            return ToolResult(
                output=f"No background subagent with id {subagent_id}.",
                error=True,
            )
        if status == "running":
            return ToolResult(output=f"Subagent {subagent_id}: running.")
        if status == "failed":
            err = manager.get_error(subagent_id) or "unknown error"
            return ToolResult(
                output=f"Subagent {subagent_id}: failed — {err}",
                metadata={"status": "failed"},
            )
        # completed
        result = manager.get_result(subagent_id)
        summary = getattr(result, "summary", "") if result else ""
        return ToolResult(
            output=f"Subagent {subagent_id}: completed.\n\n{summary}",
            metadata={"status": "completed"},
        )
