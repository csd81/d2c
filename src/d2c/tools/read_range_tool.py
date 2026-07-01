"""ReadRange — read a precise 1-based inclusive line range of a text file.

Phase 63 (context economy): lets the agent pull just the relevant slice of a
file instead of dumping the whole thing, cutting token cost. Shares Read's
path safety and read-tracking, so ``ReadRange(file)`` satisfies the
Read-before-Write guard for that canonical file (symlink/spelling bypasses are
still blocked, since read-tracking canonicalizes internally).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from d2c.tools import PermissionCategory, Tool, ToolResult

_MAX_RANGE_LINES = 300


class ReadRangeTool(Tool):
    name: ClassVar[str] = "ReadRange"
    description: ClassVar[str] = (
        "Read a specific 1-based inclusive line range of a text file "
        "(start_line..end_line), instead of the whole file. Prefer this over "
        "Read when you already know the relevant lines (e.g. from Grep or "
        f"CodeSymbols). At most {_MAX_RANGE_LINES} lines are returned; a larger "
        "range is truncated. file_path must be absolute. Reading a file this "
        "way also satisfies the Read-before-Write requirement for editing it."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The absolute path to the file to read.",
            },
            "start_line": {
                "type": "integer",
                "description": "First line to read (1-based, inclusive).",
            },
            "end_line": {
                "type": "integer",
                "description": "Last line to read (1-based, inclusive).",
            },
            "include_line_numbers": {
                "type": "boolean",
                "description": "Prefix each line with its line number (default true).",
            },
        },
        "required": ["file_path", "start_line", "end_line"],
    }
    category: ClassVar[PermissionCategory] = PermissionCategory.READ
    is_concurrent_safe: ClassVar[bool] = True

    async def execute(  # type: ignore[override]  # dispatched as execute(**tool_input); schema validates
        self,
        file_path: str = "",
        start_line: int = 1,
        end_line: int = 1,
        include_line_numbers: bool = True,
        **kwargs: Any,
    ) -> ToolResult:
        path = Path(file_path)

        if not path.is_absolute():
            return ToolResult(
                output=f"Error: file_path must be an absolute path, got: {file_path}",
                error=True,
            )
        if not path.exists():
            return ToolResult(output=f"Error: file not found: {file_path}", error=True)
        if path.is_dir():
            return ToolResult(
                output=f"Error: path is a directory, not a file: {file_path}", error=True
            )

        # Validate the requested range (1-based inclusive).
        try:
            start = int(start_line)
            end = int(end_line)
        except (TypeError, ValueError):
            return ToolResult(output="Error: start_line and end_line must be integers.", error=True)
        if start < 1:
            return ToolResult(output=f"Error: start_line must be >= 1, got: {start}", error=True)
        if end < start:
            return ToolResult(
                output=f"Error: end_line ({end}) must be >= start_line ({start}).", error=True
            )

        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return ToolResult(output=f"Error reading file: {e}", error=True)

        lines = text.split("\n")
        total_lines = len(lines)

        if start > total_lines:
            return ToolResult(
                output=f"Error: start_line {start} exceeds file length {total_lines}.",
                error=True,
            )

        # Clamp end to EOF, then cap the span to _MAX_RANGE_LINES.
        effective_end = min(end, total_lines)
        truncated = False
        if effective_end - start + 1 > _MAX_RANGE_LINES:
            effective_end = start + _MAX_RANGE_LINES - 1
            truncated = True

        selected = lines[start - 1 : effective_end]

        if include_line_numbers:
            body = "\n".join(f"{start + i} | {line}" for i, line in enumerate(selected))
        else:
            body = "\n".join(selected)

        header = f"{file_path}:{start}-{effective_end}"
        output = f"{header}\n{body}"
        if truncated:
            output += (
                f"\n\n[Truncated to {_MAX_RANGE_LINES} lines; requested {start}-{end} "
                f"of {total_lines} total.]"
            )

        result = ToolResult(
            output=output,
            metadata={
                "file_path": file_path,
                "start_line": start,
                "end_line": effective_end,
                "returned_lines": len(selected),
                "total_lines": total_lines,
                "truncated": truncated,
            },
        )

        # Phase 34/63: register the file as read (canonicalized internally) so
        # Write/Edit's "must Read first" guard is satisfied, and surface any
        # nested project memory — identical to the Read tool.
        from d2c.tools import notify_file_access
        from d2c.tools.write_tool import mark_file_read

        mark_file_read(str(path))
        return notify_file_access(path, result)
