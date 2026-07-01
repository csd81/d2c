"""Task tracking tools. Paper Section 3.2.

TaskCreate, TaskUpdate, TaskList — structured task tracking for progress
visibility during complex multi-step workflows.

Task state machine: pending → in_progress → completed | deleted
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, ClassVar

from d2c.tools import PermissionCategory, Tool, ToolResult


async def _fire_task_hook(event_name: str, payload: dict) -> None:
    """Phase 34: fire a task lifecycle hook via the active HookRegistry.

    Task tools receive no hooks handle, so we read the process-wide active
    registry (set at startup). Best-effort — never raises into the tool.
    """
    from d2c.tools import get_active_hooks
    hooks = get_active_hooks()
    if hooks is None:
        return
    try:
        from d2c.hooks import HookEvent
        await hooks.fire(HookEvent[event_name], payload)
    except Exception:
        pass


# ── In-memory task store (per-session) ───────────────────────────────────

class TaskStore:
    """In-memory task registry. Tasks are ephemeral per session."""

    _instance: "TaskStore | None" = None

    def __init__(self):
        self._tasks: list[dict] = []
        self._id_counter = 0

    @classmethod
    def get_store(cls) -> "TaskStore":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def create(self, subject: str, description: str) -> dict:
        self._id_counter += 1
        task = {
            "id": str(self._id_counter),
            "subject": subject,
            "description": description,
            "status": "pending",
            "created_at": time.time(),
        }
        self._tasks.append(task)
        return dict(task)

    def update(self, task_id: str, **kwargs) -> dict | None:
        for task in self._tasks:
            if task["id"] == task_id:
                task.update(kwargs)
                return dict(task)
        return None

    def list_all(self) -> list[dict]:
        return [dict(t) for t in self._tasks]

    def get(self, task_id: str) -> dict | None:
        for task in self._tasks:
            if task["id"] == task_id:
                return dict(task)
        return None


# ── TaskCreate tool ──────────────────────────────────────────────────────

class TaskCreateTool(Tool):
    name: ClassVar[str] = "TaskCreate"
    description: ClassVar[str] = (
        "Create a structured task for tracking progress. "
        "Use this to break down complex multi-step work into trackable units."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "subject": {
                "type": "string",
                "description": "A brief, actionable title for the task.",
            },
            "description": {
                "type": "string",
                "description": "What needs to be done — details, context, requirements.",
            },
        },
        "required": ["subject", "description"],
    }
    category: ClassVar[PermissionCategory] = PermissionCategory.META
    is_concurrent_safe: ClassVar[bool] = False

    async def execute(self, subject: str, description: str) -> ToolResult:
        store = TaskStore.get_store()
        task = store.create(subject, description)
        await _fire_task_hook("TASK_CREATED", {"task": task})
        return ToolResult(
            output=f"Task #{task['id']} created: {subject}\nStatus: {task['status']}",
            metadata={"task": task},
        )


# ── TaskUpdate tool ──────────────────────────────────────────────────────

class TaskUpdateTool(Tool):
    name: ClassVar[str] = "TaskUpdate"
    description: ClassVar[str] = (
        "Update a task's status or details. "
        "Status transitions: pending → in_progress → completed. "
        "Use 'deleted' to remove a task."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "taskId": {
                "type": "string",
                "description": "The ID of the task to update.",
            },
            "status": {
                "type": "string",
                "enum": ["pending", "in_progress", "completed", "deleted"],
                "description": "New status for the task.",
            },
            "subject": {
                "type": "string",
                "description": "New subject for the task (optional).",
            },
            "description": {
                "type": "string",
                "description": "New description for the task (optional).",
            },
        },
        "required": ["taskId"],
    }
    category: ClassVar[PermissionCategory] = PermissionCategory.META
    is_concurrent_safe: ClassVar[bool] = False

    VALID_TRANSITIONS: ClassVar[dict[str, set[str]]] = {
        "pending": {"in_progress", "deleted"},
        "in_progress": {"completed", "pending", "deleted"},
        "completed": {"deleted"},
        "deleted": set(),
    }

    async def execute(
        self,
        taskId: str,
        status: str | None = None,
        subject: str | None = None,
        description: str | None = None,
    ) -> ToolResult:
        store = TaskStore.get_store()
        existing = store.get(taskId)
        if existing is None:
            return ToolResult(
                output=f"Task #{taskId} not found. Use TaskList to see all tasks.",
                error=True,
            )

        # Validate status transition
        if status:
            current = existing["status"]
            valid_next = self.VALID_TRANSITIONS.get(current, set())
            if status not in valid_next:
                return ToolResult(
                    output=f"Invalid status transition: {current} → {status}. "
                           f"Allowed: {', '.join(sorted(valid_next)) or 'none'}",
                    error=True,
                )

            if status == "deleted":
                store._tasks = [t for t in store._tasks if t["id"] != taskId]
                return ToolResult(
                    output=f"Task #{taskId} deleted.",
                    metadata={"task_id": taskId, "status": "deleted"},
                )

        updates = {}
        if status:
            updates["status"] = status
        if subject:
            updates["subject"] = subject
        if description:
            updates["description"] = description

        updated = store.update(taskId, **updates)
        if updated:
            if status == "completed":
                await _fire_task_hook("TASK_COMPLETED", {"task": updated})
            return ToolResult(
                output=f"Task #{taskId} updated: {updated['subject']} → {updated['status']}",
                metadata={"task": updated},
            )

        return ToolResult(output=f"No changes for task #{taskId}.", metadata={"task_id": taskId})


# ── TaskList tool ────────────────────────────────────────────────────────

class TaskListTool(Tool):
    name: ClassVar[str] = "TaskList"
    description: ClassVar[str] = (
        "List all current tasks with their statuses. "
        "Use this to review progress and find the next task to work on."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {},
        "required": [],
    }
    category: ClassVar[PermissionCategory] = PermissionCategory.READ
    is_concurrent_safe: ClassVar[bool] = True

    async def execute(self) -> ToolResult:
        store = TaskStore.get_store()
        tasks = store.list_all()

        if not tasks:
            return ToolResult(
                output="No tasks created yet. Use TaskCreate to add tasks.",
                metadata={"count": 0},
            )

        status_order = {"in_progress": 0, "pending": 1, "completed": 2}
        tasks.sort(key=lambda t: status_order.get(t["status"], 3))

        lines: list[str] = []
        for t in tasks:
            icon = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}.get(t["status"], "[?]")
            lines.append(f"#{t['id']} {icon} {t['subject']} ({t['status']})")

        return ToolResult(
            output="\n".join(lines),
            metadata={"count": len(tasks), "tasks": tasks},
        )
