"""Content search with ripgrep. Paper Section 3.2 — Grep tool.

Wraps ripgrep (rg) for fast content search. Falls back to Python's re
when rg is not available. Supports regex, file type filtering, context
lines, and multiline matching.
"""

from __future__ import annotations

import asyncio
import os
import platform
from pathlib import Path
from typing import Any, ClassVar

from d2c.tools import PermissionCategory, Tool, ToolResult


class GrepTool(Tool):
    name: ClassVar[str] = "Grep"
    description: ClassVar[str] = (
        "A powerful search tool built on ripgrep. "
        "Supports full regex syntax, file type filtering, context lines, "
        "and multiple output modes (content, files_with_matches, count)."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "The regular expression pattern to search for.",
            },
            "path": {
                "type": "string",
                "description": "File or directory to search in. Defaults to current working directory.",
            },
            "glob": {
                "type": "string",
                "description": "Glob pattern to filter files (e.g. '*.js').",
            },
            "output_mode": {
                "type": "string",
                "enum": ["content", "files_with_matches", "count"],
                "description": "Output mode. Defaults to 'files_with_matches'.",
            },
            "-A": {
                "type": "integer",
                "description": "Number of lines to show after each match.",
            },
            "-B": {
                "type": "integer",
                "description": "Number of lines to show before each match.",
            },
            "-C": {
                "type": "integer",
                "description": "Number of lines to show before and after each match.",
            },
            "-n": {
                "type": "boolean",
                "description": "Show line numbers in output. Defaults to true.",
            },
            "-i": {
                "type": "boolean",
                "description": "Case insensitive search.",
            },
            "head_limit": {
                "type": "integer",
                "description": "Limit output to first N lines/entries. Defaults to 250.",
            },
            "multiline": {
                "type": "boolean",
                "description": "Enable multiline mode where patterns can span lines. Default: false.",
            },
        },
        "required": ["pattern"],
    }
    category: ClassVar[PermissionCategory] = PermissionCategory.READ
    is_concurrent_safe: ClassVar[bool] = True

    def __init__(self, cwd: Path | None = None):
        self._cwd = cwd or Path.cwd()

    async def execute(
        self,
        pattern: str,
        path: str = ".",
        glob: str | None = None,
        output_mode: str = "files_with_matches",
        A: int | None = None,
        B: int | None = None,
        C: int | None = None,
        n: bool = True,
        i: bool = False,
        head_limit: int = 250,
        multiline: bool = False,
    ) -> ToolResult:
        search_path = Path(path)
        if not search_path.is_absolute():
            search_path = self._cwd / path
        search_path = search_path.resolve()

        if not search_path.exists():
            return ToolResult(
                output=f"Error: path not found: {path}",
                error=True,
            )

        # Try ripgrep first, fall back to Python regex
        result = await self._try_ripgrep(
            pattern=pattern,
            search_path=search_path,
            glob=glob,
            output_mode=output_mode,
            A=A,
            B=B,
            C=C,
            n=n,
            i=i,
            head_limit=head_limit,
            multiline=multiline,
        )

        if result is not None:
            return result

        # Fallback: Python regex
        return self._python_grep(
            pattern=pattern,
            search_path=search_path,
            glob=glob,
            output_mode=output_mode,
            A=A,
            B=B,
            C=C,
            n=n,
            i=i,
            head_limit=head_limit,
            multiline=multiline,
        )

    async def _try_ripgrep(
        self,
        pattern: str,
        search_path: Path,
        glob: str | None,
        output_mode: str,
        A: int | None,
        B: int | None,
        C: int | None,
        n: bool,
        i: bool,
        head_limit: int,
        multiline: bool,
    ) -> ToolResult | None:
        """Try to use ripgrep. Returns None if rg is not available."""
        rg_path = self._find_rg()
        if not rg_path:
            return None

        args: list[str] = [rg_path, "--no-heading", "--with-filename"]

        if output_mode == "files_with_matches":
            args.append("-l")
        elif output_mode == "count":
            args.append("-c")

        if n:
            args.append("--line-number")
        if i:
            args.append("--ignore-case")
        if multiline:
            args.extend(["--multiline", "--multiline-dotall"])
        if glob:
            args.extend(["--glob", glob])
        if A is not None:
            args.extend(["-A", str(A)])
        if B is not None:
            args.extend(["-B", str(B)])
        if C is not None:
            args.extend(["-C", str(C)])

        # Escape pattern for shell — use --regexp to avoid issues
        args.extend(["--regexp", pattern, str(search_path)])

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=30,
            )
        except asyncio.TimeoutError:
            return ToolResult(
                output="Error: grep timed out.",
                error=True,
                metadata={"timed_out": True},
            )
        except OSError:
            return None

        output_text = stdout.decode("utf-8", errors="replace")
        if proc.returncode == 1:
            # rg returns 1 when no matches found
            return ToolResult(
                output="" if output_mode == "files_with_matches" else "No matches found.",
                metadata={"count": 0, "pattern": pattern, "engine": "ripgrep"},
            )
        elif proc.returncode != 0:
            return ToolResult(
                output=f"Error: {stderr.decode('utf-8', errors='replace')}",
                error=True,
            )

        # Apply head limit
        lines = output_text.strip().split("\n")
        if head_limit and head_limit > 0 and len(lines) > head_limit:
            lines = lines[:head_limit]
            output_text = "\n".join(lines)
            output_text += f"\n... (truncated, {len(output_text.strip().split(chr(10)))} total)"

        count = len(output_text.strip().split("\n")) if output_text.strip() else 0

        return ToolResult(
            output=output_text if output_text.strip() else "No matches found.",
            metadata={"count": count, "pattern": pattern, "engine": "ripgrep"},
        )

    def _python_grep(
        self,
        pattern: str,
        search_path: Path,
        glob: str | None,
        output_mode: str,
        A: int | None,
        B: int | None,
        C: int | None,
        n: bool,
        i: bool,
        head_limit: int,
        multiline: bool,
    ) -> ToolResult:
        """Python fallback for content search."""
        import fnmatch
        import re

        flags = re.IGNORECASE if i else 0
        if multiline:
            flags |= re.DOTALL

        try:
            regex = re.compile(pattern, flags)
        except re.error as e:
            return ToolResult(
                output=f"Invalid regex pattern: {e}",
                error=True,
            )

        results: list[str] = []
        match_count = 0

        # Collect files
        if search_path.is_file():
            files = [search_path]
        else:
            files = list(search_path.rglob("*"))

        # Filter by glob
        if glob:
            files = [f for f in files if fnmatch.fnmatch(f.name, glob)]

        for file_path in sorted(files):
            if not file_path.is_file():
                continue
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            if multiline:
                matches = list(regex.finditer(content))
                if matches:
                    match_count += len(matches)
                    if output_mode == "files_with_matches":
                        results.append(str(file_path))
                    elif output_mode == "count":
                        results.append(f"{file_path}:{len(matches)}")
                    else:
                        results.append(f"--- {file_path} ---")
                        for m in matches:
                            line_num = content[: m.start()].count("\n") + 1
                            prefix = f"{file_path}:{line_num}:" if n else ""
                            start = max(0, m.start())
                            end = min(len(content), m.end())
                            results.append(f"{prefix}{content[start:end]}")
            else:
                lines = content.split("\n")
                file_has_match = False
                for line_idx, line in enumerate(lines):
                    if regex.search(line):
                        match_count += 1
                        file_has_match = True
                        if output_mode == "files_with_matches":
                            results.append(str(file_path))
                            break
                        elif output_mode == "content":
                            prefix = f"{file_path}:{line_idx + 1}:" if n else ""
                            results.append(f"{prefix}{line}")
                if output_mode == "count" and file_has_match:
                    results.append(f"{file_path}:{match_count}")

            if head_limit and len(results) >= head_limit:
                results = results[:head_limit]
                results.append("... (truncated)")
                break

        if not results:
            return ToolResult(
                output="No matches found.",
                metadata={"count": 0, "pattern": pattern, "engine": "python"},
            )

        return ToolResult(
            output="\n".join(results),
            metadata={"count": match_count, "pattern": pattern, "engine": "python"},
        )

    @staticmethod
    def _find_rg() -> str | None:
        """Find ripgrep binary on the system."""
        rg_name = "rg.exe" if platform.system() == "Windows" else "rg"

        # Check common locations
        paths_to_check = [
            rg_name,
            os.path.expanduser("~/.cargo/bin/" + rg_name),
        ]

        # Also check PATH
        import shutil

        which = shutil.which(rg_name)
        if which:
            paths_to_check.insert(0, which)

        for p in paths_to_check:
            try:
                if os.path.isfile(p) or shutil.which(p):
                    return p
            except OSError:
                continue

        return None
