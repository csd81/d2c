"""Perform exact string replacements in an existing file.

The Edit tool uses exact string matching. It fails if old_string is not unique
in the file, requiring more surrounding context for disambiguation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from d2c.tools import PermissionCategory, Tool, ToolResult
from d2c.tools.write_tool import _read_files


class FileEditTool(Tool):
    name: ClassVar[str] = "Edit"
    description: ClassVar[str] = (
        "Performs exact string replacements in an existing file. "
        "The file_path must be an absolute path. "
        "The edit will FAIL if old_string is not unique in the file. "
        "Provide a larger string with more surrounding context to make it unique, "
        "or use replace_all to change every instance. "
        "You must Read the file first before editing."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The absolute path to the file to modify.",
            },
            "old_string": {
                "type": "string",
                "description": "The text to replace.",
            },
            "new_string": {
                "type": "string",
                "description": "The text to replace it with (must be different from old_string).",
            },
            "replace_all": {
                "type": "boolean",
                "description": "Replace all occurrences of old_string (default false).",
            },
        },
        "required": ["file_path", "old_string", "new_string"],
    }
    category: ClassVar[PermissionCategory] = PermissionCategory.WRITE
    is_concurrent_safe: ClassVar[bool] = False

    async def execute(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> ToolResult:
        path = Path(file_path)

        # Phase 23: Checkpoint before modification
        from d2c.tools import get_file_history_tracker
        tracker = get_file_history_tracker()
        if tracker:
            tracker.before_write(path)

        if not path.is_absolute():
            return ToolResult(
                output=f"Error: file_path must be an absolute path, got: {file_path}",
                error=True,
            )

        if not path.exists():
            return ToolResult(
                output=f"Error: file not found: {file_path}",
                error=True,
            )

        if path.is_dir():
            return ToolResult(
                output=f"Error: path is a directory: {file_path}",
                error=True,
            )

        if str(path) not in _read_files:
            return ToolResult(
                output=f"Error: must Read the file first before editing: {file_path}. "
                       f"Use the Read tool to read '{file_path}' first.",
                error=True,
            )

        if old_string == new_string:
            return ToolResult(
                output="Error: old_string and new_string must be different.",
                error=True,
            )

        try:
            content = path.read_text(encoding="utf-8")
        except OSError as e:
            return ToolResult(output=f"Error reading file: {e}", error=True)

        if old_string not in content:
            return ToolResult(
                output="Error: old_string was not found in the file. "
                       "It may have been modified or the string may not match exactly.",
                error=True,
            )

        occurrences = content.count(old_string)

        if occurrences > 1 and not replace_all:
            # Find line numbers for the occurrences
            lines = content.split("\n")
            positions = []
            pos = 0
            for i, line in enumerate(lines):
                line_pos = content.find(old_string, pos)
                if 0 <= line_pos < pos + len(line) + 1:
                    positions.append(i + 1)
                pos += len(line) + 1

            return ToolResult(
                output=f"Error: old_string is not unique in the file. "
                       f"Found {occurrences} occurrences. "
                       f"Provide more surrounding context to make the match unique, or use replace_all=True.\n"
                       f"Occurrences at approximately lines: {positions[:10]}",
                error=True,
            )

        new_content = content.replace(old_string, new_string) if replace_all else content.replace(old_string, new_string, 1)

        try:
            path.write_text(new_content, encoding="utf-8")
        except OSError as e:
            return ToolResult(output=f"Error writing file: {e}", error=True)

        replaced = occurrences if replace_all else 1
        return ToolResult(
            output=f"Successfully replaced {replaced} occurrence(s) in {file_path}.",
            metadata={
                "occurrences_replaced": replaced,
                "total_occurrences": occurrences,
                "bytes_before": len(content),
                "bytes_after": len(new_content),
            },
        )
