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
