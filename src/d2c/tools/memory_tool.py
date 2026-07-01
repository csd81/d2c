"""Auto-memory tool. Phase 34.

Lets the model persist durable memories across sessions via AutoMemoryStore
(~/.d2c/memory/ with a MEMORY.md index). The index is also injected into the
user context at session start (see context.getUserContext), so saved memories
are recalled automatically.
"""

from __future__ import annotations

from typing import Any, ClassVar

from d2c.tools import PermissionCategory, Tool, ToolResult


class MemoryTool(Tool):
    name: ClassVar[str] = "Remember"
    description: ClassVar[str] = (
        "Persist a durable memory across sessions, or list/delete existing ones. "
        "Use action='save' with name, memory_type (user|feedback|project|reference), "
        "description, and content. Use action='list' to see saved memories, or "
        "action='delete' with a name to remove one."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["save", "list", "delete"],
                "description": "The memory operation to perform.",
            },
            "name": {"type": "string", "description": "Short unique memory name."},
            "memory_type": {
                "type": "string",
                "enum": ["user", "feedback", "project", "reference"],
                "description": "Category of the memory (for save).",
            },
            "description": {
                "type": "string",
                "description": "One-line summary used in the index (for save).",
            },
            "content": {"type": "string", "description": "The memory body (for save)."},
        },
        "required": ["action"],
    }
    category: ClassVar[PermissionCategory] = PermissionCategory.META
    is_concurrent_safe: ClassVar[bool] = False

    async def execute(  # type: ignore[override]  # dispatched as execute(**tool_input); schema validates
        self,
        action: str,
        name: str | None = None,
        memory_type: str = "project",
        description: str = "",
        content: str = "",
        **kwargs: Any,
    ) -> ToolResult:
        from d2c.memory import AutoMemoryStore

        store = AutoMemoryStore()

        if action == "save":
            if not name or not content:
                return ToolResult(
                    output="Error: 'save' requires both name and content.",
                    error=True,
                )
            path = store.save(name, memory_type, description, content)
            return ToolResult(
                output=f"Saved memory '{name}' ({memory_type}).",
                metadata={"path": str(path)},
            )

        if action == "delete":
            if not name:
                return ToolResult(output="Error: 'delete' requires a name.", error=True)
            removed = store.delete(name)
            return ToolResult(
                output=f"Deleted memory '{name}'." if removed else f"No memory named '{name}'.",
            )

        # list
        if store.INDEX_FILE.exists():
            index = store.INDEX_FILE.read_text(encoding="utf-8").strip()
            return ToolResult(output=index or "No memories saved yet.")
        return ToolResult(output="No memories saved yet.")
