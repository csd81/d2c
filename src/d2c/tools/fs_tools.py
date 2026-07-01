"""Filesystem inspection tools (Phase 41): ListDir, FileInfo.

Read-only, cross-platform, structured alternatives to `ls`/`stat` via Bash.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar

from d2c.tools import PermissionCategory, Tool, ToolResult

_LISTDIR_MAX_ENTRIES = 500
_HASH_MAX_BYTES = 5_000_000  # only hash files up to ~5 MB


def _iso_mtime(p: Path) -> str | None:
    try:
        return datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        return None


class ListDirTool(Tool):
    name: ClassVar[str] = "ListDir"
    description: ClassVar[str] = (
        "List a directory's entries with type and size. Cross-platform and more "
        "predictable than `ls`. Supports hidden files and a small depth limit."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute directory path to list."},
            "show_hidden": {"type": "boolean", "description": "Include dotfiles (default false)."},
            "depth": {"type": "integer", "description": "Recursion depth 1-3 (default 1)."},
        },
        "required": ["path"],
    }
    category: ClassVar[PermissionCategory] = PermissionCategory.READ
    is_concurrent_safe: ClassVar[bool] = True

    async def execute(
        self, path: str = "", show_hidden: bool = False, depth: int = 1, **kwargs: Any,
    ) -> ToolResult:
        p = Path(path)
        if not p.is_absolute():
            return ToolResult(output=f"Error: path must be absolute, got: {path}", error=True)
        if not p.exists():
            return ToolResult(output=f"Error: path does not exist: {path}", error=True)
        if not p.is_dir():
            return ToolResult(output=f"Error: not a directory: {path}", error=True)

        try:
            depth = max(1, min(int(depth), 3))
        except (TypeError, ValueError):
            depth = 1

        entries: list[dict] = []
        truncated = False

        def walk(d: Path, level: int, prefix: str) -> None:
            nonlocal truncated
            try:
                children = sorted(d.iterdir(), key=lambda c: (c.is_file(), c.name.lower()))
            except OSError:
                return
            for c in children:
                if not show_hidden and c.name.startswith("."):
                    continue
                if len(entries) >= _LISTDIR_MAX_ENTRIES:
                    truncated = True
                    return
                is_dir = c.is_dir()
                size = None
                if not is_dir:
                    try:
                        size = c.stat().st_size
                    except OSError:
                        size = None
                entries.append({
                    "name": f"{prefix}{c.name}" + ("/" if is_dir else ""),
                    "type": "dir" if is_dir else "file",
                    "size": size,
                })
                if is_dir and level < depth:
                    walk(c, level + 1, f"{prefix}{c.name}/")

        walk(p, 1, "")

        lines = []
        for e in entries:
            if e["type"] == "dir":
                lines.append(f"  {e['name']}")
            else:
                sz = "" if e["size"] is None else f"  ({e['size']} bytes)"
                lines.append(f"  {e['name']}{sz}")
        body = "\n".join(lines) if lines else "  (empty)"
        if truncated:
            body += f"\n  ... [truncated at {_LISTDIR_MAX_ENTRIES} entries]"

        return ToolResult(
            output=f"{path}:\n{body}",
            metadata={"path": str(p), "count": len(entries), "truncated": truncated, "entries": entries},
        )


class FileInfoTool(Tool):
    name: ClassVar[str] = "FileInfo"
    description: ClassVar[str] = (
        "Return metadata for a path (exists, type, size, modified time, and a "
        "sha256 for regular files) without reading the file contents."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute path to inspect."},
        },
        "required": ["path"],
    }
    category: ClassVar[PermissionCategory] = PermissionCategory.READ
    is_concurrent_safe: ClassVar[bool] = True

    async def execute(self, path: str = "", **kwargs: Any) -> ToolResult:
        p = Path(path)
        if not p.is_absolute():
            return ToolResult(output=f"Error: path must be absolute, got: {path}", error=True)

        if not p.exists() and not p.is_symlink():
            return ToolResult(
                output=f"Path does not exist: {path}",
                metadata={"path": str(p), "exists": False},
            )

        is_symlink = p.is_symlink()
        if p.is_dir():
            ptype = "dir"
        elif p.is_file():
            ptype = "file"
        else:
            ptype = "other"

        size = None
        sha256 = None
        if ptype == "file":
            try:
                size = p.stat().st_size
                if size is not None and size <= _HASH_MAX_BYTES:
                    h = hashlib.sha256()
                    h.update(p.read_bytes())
                    sha256 = h.hexdigest()
            except OSError:
                pass

        meta = {
            "path": str(p),
            "exists": True,
            "type": ptype,
            "symlink": is_symlink,
            "size": size,
            "modified": _iso_mtime(p),
            "sha256": sha256,
        }
        lines = [
            f"Path: {path}",
            f"Type: {ptype}" + (" (symlink)" if is_symlink else ""),
            f"Size: {size if size is not None else 'n/a'} bytes",
            f"Modified: {meta['modified']}",
        ]
        if sha256:
            lines.append(f"SHA256: {sha256}")
        return ToolResult(output="\n".join(lines), metadata=meta)
