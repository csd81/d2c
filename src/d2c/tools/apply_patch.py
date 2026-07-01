"""ApplyPatch tool (Phase 51): apply a unified diff to files.

A safer alternative to shelling out to ``patch``. Paths are cwd-relative
(absolute and ``..`` traversal rejected). Existing files being modified/deleted
must have been Read first; every touched file is checkpointed before mutation;
the whole patch is atomic (if any hunk fails to apply, nothing is written); a
FILE_CHANGED hook fires per file on success.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from d2c.tools import PermissionCategory, Tool, ToolResult

_DEV_NULL = "/dev/null"


@dataclass
class _Hunk:
    old_start: int
    lines: list[tuple[str, str]]  # (op, text): op in " ", "-", "+"


@dataclass
class _FilePatch:
    path: str
    hunks: list[_Hunk] = field(default_factory=list)
    is_new: bool = False
    is_delete: bool = False


def _strip_prefix(p: str) -> str:
    p = p.strip()
    # drop a trailing tab-timestamp some diffs add, then a/ or b/ prefix
    p = p.split("\t", 1)[0]
    if p.startswith(("a/", "b/")):
        p = p[2:]
    return p


def parse_unified_diff(text: str) -> list[_FilePatch]:
    """Parse a (git or plain) unified diff into per-file patches.

    Raises ValueError on a malformed hunk header.
    """
    files: list[_FilePatch] = []
    cur: _FilePatch | None = None
    old_path: str | None = None
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("diff --git") or line.startswith("index "):
            i += 1
            continue
        if line.startswith("--- "):
            old_path = line[4:].strip()
            i += 1
            # next line should be +++
            if i < len(lines) and lines[i].startswith("+++ "):
                new_raw = lines[i][4:].strip()
                i += 1
                is_new = old_path.split("\t", 1)[0].strip() == _DEV_NULL
                is_delete = new_raw.split("\t", 1)[0].strip() == _DEV_NULL
                path = _strip_prefix(old_path) if is_delete else _strip_prefix(new_raw)
                cur = _FilePatch(path=path, is_new=is_new, is_delete=is_delete)
                files.append(cur)
            continue
        if line.startswith("@@"):
            if cur is None:
                raise ValueError("hunk before any file header")
            # @@ -old_start[,old_len] +new_start[,new_len] @@
            try:
                seg = line.split("@@")[1].strip()
                old_part = seg.split(" ")[0]  # -l,s
                old_start = int(old_part[1:].split(",")[0])
            except (IndexError, ValueError) as e:
                raise ValueError(f"malformed hunk header: {line!r}") from e
            hunk = _Hunk(old_start=old_start, lines=[])
            i += 1
            while i < len(lines) and not lines[i].startswith(("@@", "--- ", "diff --git")):
                hl = lines[i]
                if hl.startswith("\\"):  # "\ No newline at end of file"
                    i += 1
                    continue
                op = hl[0] if hl else " "
                if op not in (" ", "-", "+"):
                    break  # end of hunk body
                hunk.lines.append((op, hl[1:]))
                i += 1
            cur.hunks.append(hunk)
            continue
        i += 1
    return files


def _find_block(lines: list[str], block: list[str], hint: int) -> int | None:
    """Locate `block` (list of exact lines) in `lines`. Empty block → insert at
    the clamped hint. Try the hint position first, then scan."""
    if not block:
        return max(0, min(hint, len(lines)))
    n = len(block)
    candidates = [hint] + list(range(0, len(lines) - n + 1))
    for pos in candidates:
        if 0 <= pos <= len(lines) - n and lines[pos : pos + n] == block:
            return pos
    return None


def _apply_hunks(orig: list[str], hunks: list[_Hunk]) -> list[str] | None:
    """Apply hunks to `orig` lines. Returns new lines, or None if any hunk
    fails to match (atomic-per-file)."""
    result = list(orig)
    offset = 0
    for h in hunks:
        old_block = [t for op, t in h.lines if op in (" ", "-")]
        new_block = [t for op, t in h.lines if op in (" ", "+")]
        pos = _find_block(result, old_block, h.old_start - 1 + offset)
        if pos is None:
            return None
        result[pos : pos + len(old_block)] = new_block
        offset += len(new_block) - len(old_block)
    return result


class ApplyPatchTool(Tool):
    name: ClassVar[str] = "ApplyPatch"
    description: ClassVar[str] = (
        "Apply a unified diff (git/plain `diff -u` format) to one or more files. "
        "Paths are relative to the working directory (absolute and '..' rejected). "
        "You must Read an existing file before modifying/deleting it. The patch is "
        "applied atomically: if any hunk fails, no file is changed."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "patch": {"type": "string", "description": "The unified diff to apply."},
        },
        "required": ["patch"],
    }
    category: ClassVar[PermissionCategory] = PermissionCategory.WRITE
    is_concurrent_safe: ClassVar[bool] = False

    def __init__(self, cwd: Path | None = None):
        self._cwd = (cwd or Path.cwd()).resolve()

    def _resolve(self, rel: str) -> Path | None:
        """Resolve a cwd-relative patch path, rejecting absolute/traversal."""
        p = Path(rel)
        if p.is_absolute() or ".." in p.parts:
            return None
        full = (self._cwd / p).resolve()
        try:
            full.relative_to(self._cwd)
        except ValueError:
            return None
        return full

    async def execute(self, patch: str = "", **kwargs: Any) -> ToolResult:
        from d2c.tools.write_tool import is_file_read

        if not patch.strip():
            return ToolResult(output="Error: patch is required.", error=True)
        try:
            file_patches = parse_unified_diff(patch)
        except ValueError as e:
            return ToolResult(output=f"Error: {e}", error=True)
        if not file_patches:
            return ToolResult(output="Error: no file patches found in diff.", error=True)

        # ── Phase 1: validate + compute new content for every file (no writes) ──
        planned: list[tuple[Path, str, str | None]] = []  # (path, op, new_content|None-for-delete)
        for fp in file_patches:
            full = self._resolve(fp.path)
            if full is None:
                return ToolResult(
                    output=f"Error: unsafe path in patch (absolute or traversal): {fp.path}",
                    error=True,
                )
            exists = full.exists()
            if fp.is_delete:
                if not exists:
                    return ToolResult(
                        output=f"Error: cannot delete missing file: {fp.path}", error=True
                    )
                if not is_file_read(full):
                    return ToolResult(
                        output=f"Error: must Read '{fp.path}' before deleting it.", error=True
                    )
                planned.append((full, "delete", None))
                continue
            if fp.is_new:
                if exists:
                    return ToolResult(
                        output=f"Error: patch creates '{fp.path}' but it already exists.",
                        error=True,
                    )
                orig: list[str] = []
            else:
                if not exists:
                    return ToolResult(output=f"Error: target file not found: {fp.path}", error=True)
                if not is_file_read(full):
                    return ToolResult(
                        output=f"Error: must Read '{fp.path}' before patching it.", error=True
                    )
                try:
                    orig = full.read_text(encoding="utf-8").split("\n")
                except OSError as e:
                    return ToolResult(output=f"Error reading {fp.path}: {e}", error=True)

            new_lines = _apply_hunks(orig, fp.hunks)
            if new_lines is None:
                return ToolResult(
                    output=f"Error: hunk did not apply cleanly to '{fp.path}'; no files changed.",
                    error=True,
                )
            planned.append((full, "new" if fp.is_new else "modify", "\n".join(new_lines)))

        # ── Phase 2: checkpoint + write all (validation passed → atomic) ──
        from d2c.tools import fire_active_hook, get_file_history_tracker
        from d2c.tools.write_tool import mark_file_read

        tracker = get_file_history_tracker()
        changed: list[str] = []
        for full, op, content in planned:
            if tracker:
                tracker.before_write(full)
            try:
                if op == "delete":
                    full.unlink()
                else:
                    full.parent.mkdir(parents=True, exist_ok=True)
                    full.write_text(content or "", encoding="utf-8")
                    mark_file_read(full)
            except OSError as e:
                return ToolResult(
                    output=f"Error writing {full}: {e} (patch partially applied)", error=True
                )
            rel = str(full.relative_to(self._cwd))
            changed.append(rel)
            await fire_active_hook(
                "FILE_CHANGED", {"path": str(full), "tool": "ApplyPatch", "operation": op}
            )

        return ToolResult(
            output=f"Applied patch to {len(changed)} file(s): {', '.join(changed)}",
            metadata={"changed": changed, "file_count": len(changed)},
        )
