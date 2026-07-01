"""Write a file to the local filesystem.

Overwrites existing files. The caller must have Read the file first
(safety check to prevent accidental overwrites).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from d2c.tools import PermissionCategory, Tool, ToolResult

# Set of files that have been read this session (canonical realpaths).
_read_files: set[str] = set()


def _canonical(path: str | Path) -> str:
    """Canonicalize a path for read-tracking: resolve ``..``/``.`` and symlinks
    so alternate spellings of the same file cannot bypass the Read-before-Write
    guard (Phase 46). Falls back to the absolute string if resolution fails."""
    try:
        return str(Path(path).resolve())
    except OSError:
        return str(Path(path).absolute())


class FileWriteTool(Tool):
    name: ClassVar[str] = "Write"
    description: ClassVar[str] = (
        "Writes a file to the local filesystem. "
        "The file_path parameter must be an absolute path. "
        "This tool will overwrite the existing file if there is one at the provided path. "
        "You must use the Read tool first to read the file's contents before overwriting it."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The absolute path to the file to write (must be absolute, not relative).",
            },
            "content": {
                "type": "string",
                "description": "The content to write to the file.",
            },
        },
        "required": ["file_path", "content"],
    }
    category: ClassVar[PermissionCategory] = PermissionCategory.WRITE
    is_concurrent_safe: ClassVar[bool] = False

    async def execute(self, file_path: str, content: str, **kwargs: Any) -> ToolResult:  # type: ignore[override]  # dispatched as execute(**tool_input); schema validates
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

        if path.is_dir():
            return ToolResult(
                output=f"Error: path is a directory: {file_path}",
                error=True,
            )

        # Safety: parent directory must exist
        if not path.parent.exists():
            return ToolResult(
                output=f"Error: parent directory does not exist: {path.parent}. "
                f"Create it first with a shell command.",
                error=True,
            )

        # Safety: must have read the file first (if it exists)
        if path.exists() and not is_file_read(path):
            return ToolResult(
                output=f"Error: must Read the file first before overwriting: {file_path}. "
                f"Use the Read tool to read '{file_path}' first.",
                error=True,
            )

        try:
            path.write_text(content, encoding="utf-8")
            mark_file_read(path)  # mark as read for subsequent writes
            result = ToolResult(
                output=f"Successfully wrote {len(content)} bytes to {file_path}.",
                metadata={"bytes_written": len(content), "lines": content.count("\n") + 1},
            )
            from d2c.tools import fire_active_hook, notify_file_access

            await fire_active_hook(
                "FILE_CHANGED",
                {
                    "path": str(path),
                    "tool": "Write",
                    "operation": "write",
                },
            )
            return notify_file_access(path, result)
        except OSError as e:
            return ToolResult(output=f"Error writing file: {e}", error=True)


def mark_file_read(path: str | Path) -> None:
    """Register a file as having been read this session (by canonical realpath)."""
    _read_files.add(_canonical(path))


def is_file_read(path: str | Path) -> bool:
    """Whether a file has been Read (or written) this session (canonical realpath)."""
    return _canonical(path) in _read_files


def clear_read_files() -> None:
    """Clear the read-file tracking (for testing)."""
    _read_files.clear()
