"""Tool base classes: Tool ABC, ToolResult, ToolUse, PermissionCategory."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar


class PermissionCategory(Enum):
    READ = "read"
    WRITE = "write"
    SHELL = "shell"
    META = "meta"


@dataclass
class ToolResult:
    output: str
    attachments: list[dict[str, Any]] = field(default_factory=list)
    error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.output


@dataclass
class ToolUse:
    id: str
    name: str
    input: dict[str, Any]
    timestamp: float = field(default_factory=time.time)


class Tool(ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    input_schema: ClassVar[dict[str, Any]]
    category: ClassVar[PermissionCategory]
    is_concurrent_safe: ClassVar[bool] = False

    @abstractmethod
    async def execute(self, **kwargs: Any) -> ToolResult: ...

    def is_enabled(self) -> bool:
        return True

    def to_api_format(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def __repr__(self) -> str:
        return f"<{type(self).__name__}>"


# ── Phase 23: File history tracker integration ────────────────────────

_file_history_tracker: Any = None


def set_file_history_tracker(tracker: Any) -> None:
    """Set the global file history tracker for write/EDIT operations."""
    global _file_history_tracker
    _file_history_tracker = tracker


def get_file_history_tracker() -> Any:
    return _file_history_tracker


# ── Phase 34: Active runtime accessors ────────────────────────────────
#
# tool.execute() is called as tool.execute(**tu.input) with no context
# object, and runs via two paths (loop._execute_one_tool and
# streaming_executor). Rather than thread a context arg through both, we
# expose the current session's hooks and memory loader as module globals
# (same pattern as the file-history tracker above), set once at startup.

_active_hooks: Any = None
_active_memory_loader: Any = None


def set_active_hooks(hooks: Any) -> None:
    """Set the active HookRegistry so tools can fire lifecycle events."""
    global _active_hooks
    _active_hooks = hooks


def get_active_hooks() -> Any:
    return _active_hooks


def set_active_memory_loader(loader: Any) -> None:
    """Set the active LazyMemoryLoader for nested-directory memory loading."""
    global _active_memory_loader
    _active_memory_loader = loader


def get_active_memory_loader() -> Any:
    return _active_memory_loader


async def fire_active_hook(event_name: str, payload: dict) -> None:
    """Fire an observability hook via the active HookRegistry (Phase 40).

    Tools receive no hooks handle, so read the process-wide active registry
    (set at startup). Best-effort — an observability hook must never crash the
    tool path.
    """
    # Phase 44: mirror file/task lifecycle into the audit log (redacted).
    try:
        from d2c.observability import audit
        audit(event_name.lower(), **{k: v for k, v in payload.items()
                                     if k in ("path", "tool", "operation")})
    except Exception:
        pass
    hooks = get_active_hooks()
    if hooks is None:
        return
    try:
        from d2c.hooks import HookEvent
        await hooks.fire(HookEvent[event_name], payload)
    except Exception:
        pass


def notify_file_access(path: Any, result: "ToolResult") -> "ToolResult":
    """Surface nested CLAUDE.md / path rules for a file the agent just touched.

    Called by Read/Write/Edit after a successful operation. Appends any
    newly-discovered project instructions to the tool result so they enter
    the model's context. Best-effort: never raises into the tool path.
    """
    if result.error:
        return result
    loader = get_active_memory_loader()
    if loader is None:
        return result
    try:
        from pathlib import Path as _Path
        extra = loader.on_file_accessed(_Path(str(path)))
    except Exception:
        extra = None
    if extra:
        parent = str(path)
        result.output += f"\n\n[Project instructions loaded for {parent}]\n{extra}"
    return result
