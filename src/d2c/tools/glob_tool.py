"""Fast file pattern matching. Paper Section 3.2 — Glob tool.

Uses pathlib.glob with recursive support. Results sorted by modification time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from d2c.tools import PermissionCategory, Tool, ToolResult


class GlobTool(Tool):
    name: ClassVar[str] = "Glob"
    description: ClassVar[str] = (
        "Fast file pattern matching tool that works with any codebase size. "
        "Supports glob patterns like '**/*.js' or 'src/**/*.ts'. "
        "Returns matching file paths sorted by modification time."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "The glob pattern to match files against.",
            },
            "path": {
                "type": "string",
                "description": "The directory to search in. Defaults to current working directory.",
            },
        },
        "required": ["pattern"],
    }
    category: ClassVar[PermissionCategory] = PermissionCategory.READ
    is_concurrent_safe: ClassVar[bool] = True

    def __init__(self, cwd: Path | None = None):
        self._cwd = cwd or Path.cwd()

    async def execute(self, pattern: str, path: str = ".") -> ToolResult:
        search_dir = Path(path)
        if not search_dir.is_absolute():
            search_dir = self._cwd / path

        search_dir = search_dir.resolve()

        try:
            matches = sorted(
                search_dir.glob(pattern),
                key=lambda p: p.stat().st_mtime if p.exists() else 0,
                reverse=True,
            )
        except OSError as e:
            return ToolResult(
                output=f"Error searching files: {e}",
                error=True,
            )

        if not matches:
            return ToolResult(
                output="No files found matching pattern.",
                metadata={"count": 0, "pattern": pattern},
            )

        # Return relative paths when possible
        lines: list[str] = []
        for p in matches:
            try:
                rel = p.relative_to(self._cwd)
                lines.append(str(rel))
            except ValueError:
                lines.append(str(p))

        return ToolResult(
            output="\n".join(lines),
            metadata={"count": len(matches), "pattern": pattern, "path": str(search_dir)},
        )
