"""Structured write tools (Phase 41): ReplaceMany, JsonEdit.

Both are WRITE tools that obey the same safety invariants as Edit/Write:
absolute path, Read-before-Write on existing files, a file-history checkpoint
before mutation, a FILE_CHANGED hook after success, and read-tracking so
follow-up writes are allowed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

from d2c.tools import PermissionCategory, Tool, ToolResult


def _require_read(path: Path) -> ToolResult | None:
    """Enforce Read-before-Write on an existing file. Returns an error result
    if the guard fails, else None."""
    from d2c.tools.write_tool import is_file_read
    if path.exists() and not is_file_read(str(path)):
        return ToolResult(
            output=f"Error: must Read the file first before editing: {path}.",
            error=True,
        )
    return None


async def _finish_write(path: Path, new_content: str, output: str, metadata: dict) -> ToolResult:
    """Checkpoint, write, mark-read, fire FILE_CHANGED, surface memory."""
    from d2c.tools import get_file_history_tracker, fire_active_hook, notify_file_access
    from d2c.tools.write_tool import mark_file_read

    tracker = get_file_history_tracker()
    if tracker:
        tracker.before_write(path)
    try:
        path.write_text(new_content, encoding="utf-8")
    except OSError as e:
        return ToolResult(output=f"Error writing file: {e}", error=True)
    mark_file_read(str(path))
    await fire_active_hook("FILE_CHANGED", {
        "path": str(path), "tool": metadata.get("_tool", "Edit"), "operation": metadata.get("_op", "edit"),
    })
    result = ToolResult(output=output, metadata={k: v for k, v in metadata.items() if not k.startswith("_")})
    return notify_file_access(path, result)


class ReplaceManyTool(Tool):
    name: ClassVar[str] = "ReplaceMany"
    description: ClassVar[str] = (
        "Apply several exact string replacements to one file atomically. Each "
        "replacement's old_string must be present; if any is missing, the file is "
        "left unchanged. Safer than sed -i and fewer round-trips than repeated Edit. "
        "You must Read the file first."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Absolute path to the file."},
            "replacements": {
                "type": "array",
                "description": "Ordered list of {old_string, new_string} replacements.",
                "items": {
                    "type": "object",
                    "properties": {
                        "old_string": {"type": "string"},
                        "new_string": {"type": "string"},
                    },
                    "required": ["old_string", "new_string"],
                },
            },
        },
        "required": ["file_path", "replacements"],
    }
    category: ClassVar[PermissionCategory] = PermissionCategory.WRITE
    is_concurrent_safe: ClassVar[bool] = False

    async def execute(
        self, file_path: str = "", replacements: list[dict] | None = None, **kwargs: Any,
    ) -> ToolResult:
        path = Path(file_path)
        if not path.is_absolute():
            return ToolResult(output=f"Error: file_path must be absolute, got: {file_path}", error=True)
        if not path.exists():
            return ToolResult(output=f"Error: file not found: {file_path}", error=True)
        if path.is_dir():
            return ToolResult(output=f"Error: path is a directory: {file_path}", error=True)
        guard = _require_read(path)
        if guard:
            return guard
        if not replacements:
            return ToolResult(output="Error: replacements list is required and non-empty.", error=True)

        try:
            content = path.read_text(encoding="utf-8")
        except OSError as e:
            return ToolResult(output=f"Error reading file: {e}", error=True)

        working = content
        applied = 0
        for i, r in enumerate(replacements):
            old = r.get("old_string", "")
            new = r.get("new_string", "")
            if old == "":
                return ToolResult(output=f"Error: replacement #{i + 1} has an empty old_string.", error=True)
            if old not in working:
                # Atomic: nothing is written when any replacement can't apply.
                return ToolResult(
                    output=f"Error: replacement #{i + 1} old_string not found; file left unchanged.",
                    error=True,
                )
            working = working.replace(old, new, 1)
            applied += 1

        if working == content:
            return ToolResult(output="No changes (replacements produced identical content).")

        return await _finish_write(
            path, working,
            output=f"Applied {applied} replacement(s) to {file_path}.",
            metadata={"replacements_applied": applied, "_tool": "ReplaceMany", "_op": "replace_many"},
        )


class JsonEditTool(Tool):
    name: ClassVar[str] = "JsonEdit"
    description: ClassVar[str] = (
        "Edit a JSON file by dotted key path (set or delete). Safer than string "
        "edits for config/package files. Writes deterministic 2-space formatting. "
        "You must Read the file first."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Absolute path to the JSON file."},
            "operation": {"type": "string", "enum": ["set", "delete"], "description": "set or delete."},
            "path": {
                "type": "string",
                "description": "Dotted key path, e.g. 'scripts.test' or 'a.0.b' (list index).",
            },
            "value": {"description": "New value for 'set' (any JSON type)."},
        },
        "required": ["file_path", "operation", "path"],
    }
    category: ClassVar[PermissionCategory] = PermissionCategory.WRITE
    is_concurrent_safe: ClassVar[bool] = False

    async def execute(
        self, file_path: str = "", operation: str = "", path: str = "",
        value: Any = None, **kwargs: Any,
    ) -> ToolResult:
        fp = Path(file_path)
        if not fp.is_absolute():
            return ToolResult(output=f"Error: file_path must be absolute, got: {file_path}", error=True)
        if not fp.exists():
            return ToolResult(output=f"Error: file not found: {file_path}", error=True)
        guard = _require_read(fp)
        if guard:
            return guard
        if operation not in ("set", "delete"):
            return ToolResult(output="Error: operation must be 'set' or 'delete'.", error=True)
        if not path:
            return ToolResult(output="Error: path is required.", error=True)

        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            return ToolResult(output=f"Error: invalid JSON in {file_path}: {e}", error=True)
        except OSError as e:
            return ToolResult(output=f"Error reading file: {e}", error=True)

        keys = path.split(".")
        node = data
        # Navigate to the parent of the target key.
        try:
            for k in keys[:-1]:
                node = node[int(k)] if isinstance(node, list) else node[k]
        except (KeyError, IndexError, TypeError, ValueError):
            return ToolResult(output=f"Error: path '{path}' not found.", error=True)

        last = keys[-1]
        try:
            if operation == "set":
                if isinstance(node, list):
                    node[int(last)] = value
                elif isinstance(node, dict):
                    node[last] = value
                else:
                    return ToolResult(output=f"Error: cannot set key on non-container at '{path}'.", error=True)
            else:  # delete
                if isinstance(node, list):
                    del node[int(last)]
                elif isinstance(node, dict):
                    if last not in node:
                        return ToolResult(output=f"Error: key '{path}' not found.", error=True)
                    del node[last]
                else:
                    return ToolResult(output=f"Error: cannot delete key on non-container at '{path}'.", error=True)
        except (KeyError, IndexError, ValueError):
            return ToolResult(output=f"Error: path '{path}' not found.", error=True)

        new_content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        return await _finish_write(
            fp, new_content,
            output=f"JSON {operation} at '{path}' in {file_path}.",
            metadata={"operation": operation, "path": path, "_tool": "JsonEdit", "_op": "json_edit"},
        )
