"""Git helper tools (Phase 41): GitStatus, GitDiff.

Structured, read-only git inspection — safer and more predictable than asking
the model to run raw `git` via Bash.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, ClassVar

from d2c.tools import PermissionCategory, Tool, ToolResult

_GIT_TIMEOUT = 15.0
_DIFF_MAX_CHARS = 30_000


async def _run_git(args: list[str], cwd: Path) -> tuple[int, str, str]:
    """Run a git command; return (returncode, stdout, stderr)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd),
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=_GIT_TIMEOUT)
        return proc.returncode or 0, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
    except FileNotFoundError:
        return 127, "", "git executable not found"
    except asyncio.TimeoutError:
        return -1, "", "git command timed out"


async def _is_git_repo(cwd: Path) -> bool:
    code, out, _ = await _run_git(["rev-parse", "--is-inside-work-tree"], cwd)
    return code == 0 and out.strip() == "true"


class GitStatusTool(Tool):
    name: ClassVar[str] = "GitStatus"
    description: ClassVar[str] = (
        "Show the git working-tree status: current branch and staged, unstaged, "
        "and untracked files. Read-only; prefer this over running `git status` via Bash."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Optional repo/subdir path (default: cwd)."},
        },
        "required": [],
    }
    category: ClassVar[PermissionCategory] = PermissionCategory.READ
    is_concurrent_safe: ClassVar[bool] = True

    def __init__(self, cwd: Path | None = None):
        self._cwd = cwd or Path.cwd()

    async def execute(self, path: str | None = None, **kwargs: Any) -> ToolResult:
        cwd = Path(path) if path else self._cwd
        if not await _is_git_repo(cwd):
            return ToolResult(output=f"Not a git repository: {cwd}", error=True)

        code, out, err = await _run_git(["status", "--porcelain=v1", "--branch"], cwd)
        if code != 0:
            return ToolResult(output=f"git status failed: {err.strip()}", error=True)

        branch = "(unknown)"
        staged: list[str] = []
        unstaged: list[str] = []
        untracked: list[str] = []
        for line in out.splitlines():
            if line.startswith("## "):
                branch = line[3:].split("...")[0].strip()
                continue
            if not line:
                continue
            x, y, name = line[0], line[1], line[3:]
            if x == "?" and y == "?":
                untracked.append(name)
                continue
            if x != " ":
                staged.append(f"{x} {name}")
            if y != " ":
                unstaged.append(f"{y} {name}")

        lines = [f"Branch: {branch}"]
        lines.append(f"Staged ({len(staged)}):" + ("" if staged else " none"))
        lines += [f"  {s}" for s in staged]
        lines.append(f"Unstaged ({len(unstaged)}):" + ("" if unstaged else " none"))
        lines += [f"  {s}" for s in unstaged]
        lines.append(f"Untracked ({len(untracked)}):" + ("" if untracked else " none"))
        lines += [f"  {s}" for s in untracked]

        return ToolResult(
            output="\n".join(lines),
            metadata={
                "branch": branch,
                "staged": staged,
                "unstaged": unstaged,
                "untracked": untracked,
                "clean": not (staged or unstaged or untracked),
            },
        )


class GitDiffTool(Tool):
    name: ClassVar[str] = "GitDiff"
    description: ClassVar[str] = (
        "Show a git diff for the working tree (or a specific path). Set staged=true "
        "for the staged diff. Read-only; output is truncated to protect context."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Optional file/dir to diff."},
            "staged": {"type": "boolean", "description": "Diff staged changes (default false)."},
        },
        "required": [],
    }
    category: ClassVar[PermissionCategory] = PermissionCategory.READ
    is_concurrent_safe: ClassVar[bool] = True

    def __init__(self, cwd: Path | None = None):
        self._cwd = cwd or Path.cwd()

    async def execute(
        self, path: str | None = None, staged: bool = False, **kwargs: Any,
    ) -> ToolResult:
        if not await _is_git_repo(self._cwd):
            return ToolResult(output=f"Not a git repository: {self._cwd}", error=True)

        args = ["diff"]
        if staged:
            args.append("--staged")
        if path:
            args += ["--", path]

        code, out, err = await _run_git(args, self._cwd)
        if code != 0:
            return ToolResult(output=f"git diff failed: {err.strip()}", error=True)

        if not out.strip():
            return ToolResult(
                output="(no changes)",
                metadata={"staged": staged, "truncated": False, "diff_bytes": 0},
            )

        truncated = len(out) > _DIFF_MAX_CHARS
        shown = out[:_DIFF_MAX_CHARS]
        if truncated:
            shown += f"\n\n... [diff truncated at {_DIFF_MAX_CHARS} chars of {len(out)}]"

        return ToolResult(
            output=shown,
            metadata={"staged": staged, "truncated": truncated, "diff_bytes": len(out)},
        )
