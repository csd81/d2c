"""AgentTool — meta-tool that spawns subagents in isolated contexts. Paper Section 8.

AgentTool sits alongside SkillTool in the base tool pool as a meta-tool
that dispatches to subagent definitions. Each subagent gets independent
context, tool set, and sidechain transcript. Only the final summary
returns to the parent — full history never enters parent context.
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from d2c.tools import PermissionCategory, Tool, ToolResult, ToolUse

if TYPE_CHECKING:
    from d2c.config import Config


AGENT_TOOL_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "description": {
            "type": "string",
            "description": "A short (3-5 word) description of the task",
        },
        "prompt": {
            "type": "string",
            "description": "The task for the agent to perform",
        },
        "subagent_type": {
            "type": "string",
            "description": "The type of specialized agent: Explore, Plan, General-purpose, or custom",
        },
        "permission_mode_override": {
            "type": "string",
            "description": "Optional permission mode override for the subagent",
        },
        "max_turns": {
            "type": "integer",
            "description": "Maximum turns for the subagent (default: 25)",
        },
        "background": {
            "type": "boolean",
            "description": "Set to true to run in the background",
        },
    },
    "required": ["description", "prompt"],
}


class AgentTool(Tool):
    """Paper Section 8.1: meta-tool that delegates to subagent definitions.

    The model invokes Agent with structured input: prompt, subagent_type,
    permission_mode_override, max_turns, background.

    Returns a summary of the subagent's work — not the full transcript.
    """

    name = "Agent"
    description = (
        "Launch a new agent to handle complex, multi-step tasks autonomously. "
        "Available agent types: Explore (read/search), Plan (structured planning), "
        "General-purpose (broad capability). Use when a task can be fully delegated."
    )
    input_schema = AGENT_TOOL_INPUT_SCHEMA
    category = PermissionCategory.META
    is_concurrent_safe = True

    def __init__(self, config: "Config | None" = None):
        self._config = config

    async def execute(
        self,
        description: str = "",
        prompt: str = "",
        subagent_type: str = "General-purpose",
        permission_mode_override: str | None = None,
        max_turns: int = 25,
        background: bool = False,
        **kwargs: Any,
    ) -> ToolResult:
        """Spawn an isolated subagent and return its summary.

        When background=True, the subagent runs as a fire-and-forget task.
        The parent gets back a subagent_id and can query status/results later.
        """
        from d2c.subagent import (
            load_subagent_definition,
            spawn_subagent,
            SubagentResult,
            get_background_manager,
        )

        # Resolve definition
        try:
            definition = load_subagent_definition(subagent_type)
        except ValueError as e:
            return ToolResult(
                output=f"Error: {e}",
                error=True,
                metadata={"unknown_subagent": True},
            )

        # Apply overrides
        if permission_mode_override:
            definition.permission_mode = permission_mode_override
        definition.max_turns = max_turns
        definition.background = background

        # Get parent config/session from the tool's execution context
        from d2c.config import Config
        parent_config = self._config or Config.load()

        # ── Background execution ──────────────────────────────────────
        if background:
            bg_manager = get_background_manager()
            subagent_id = await bg_manager.launch_background(
                definition=definition,
                task_prompt=prompt,
                parent_config=parent_config,
                parent_session_store=None,
            )
            return ToolResult(
                output=(
                    f"[Subagent '{definition.name}' launched in background]\n"
                    f"ID: {subagent_id}\n"
                    f"Use the AgentStatus tool (subagent_id='{subagent_id}') to check "
                    f"progress and retrieve the result."
                ),
                metadata={
                    "subagent_type": definition.name,
                    "background": True,
                    "subagent_id": subagent_id,
                },
            )

        # ── Foreground execution ──────────────────────────────────────
        result: SubagentResult = await spawn_subagent(
            definition=definition,
            task_prompt=prompt,
            parent_config=parent_config,
            parent_session_store=None,
        )

        # Paper Section 8.3: "only the subagent's final response text and metadata
        # return to the parent conversation context"
        status = "completed" if result.success else "failed"
        lines = [
            f"[Subagent '{definition.name}' {status}]",
            f"Turns: {result.turns}, Tool calls: {result.tool_calls}",
            "",
            result.summary,
        ]

        if result.sidechain_path:
            lines.append(f"\nSidechain: {result.sidechain_path}")

        return ToolResult(
            output="\n".join(lines),
            metadata={
                "subagent_type": definition.name,
                "sidechain_path": str(result.sidechain_path) if result.sidechain_path else None,
                "tool_calls": result.tool_calls,
                "turns": result.turns,
                "success": result.success,
            },
        )
