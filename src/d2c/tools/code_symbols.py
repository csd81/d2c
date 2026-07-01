"""CodeSymbols tool (Phase 56): lightweight Python symbol listing via `ast`.

Structured alternative to `grep -n 'def \\|class '` via Bash: classes,
functions/methods, and imports with line numbers. Read-only; parses only
(no execution, no import).
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, ClassVar

from d2c.tools import PermissionCategory, Tool, ToolResult

_MAX_SYMBOLS = 300
_PY_EXTENSIONS = (".py", ".pyi")


def _func_kind(node: ast.AST) -> str:
    return "async function" if isinstance(node, ast.AsyncFunctionDef) else "function"


def _extract_symbols(tree: ast.Module) -> list[dict[str, Any]]:
    symbols: list[dict[str, Any]] = []

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            bases = [ast.unparse(b) for b in node.bases] if node.bases else []
            symbols.append(
                {
                    "kind": "class",
                    "name": node.name,
                    "line": node.lineno,
                    "bases": bases,
                }
            )
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    symbols.append(
                        {
                            "kind": f"method ({_func_kind(child)})",
                            "name": f"{node.name}.{child.name}",
                            "line": child.lineno,
                            "bases": [],
                        }
                    )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(
                {"kind": _func_kind(node), "name": node.name, "line": node.lineno, "bases": []}
            )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                symbols.append(
                    {"kind": "import", "name": alias.name, "line": node.lineno, "bases": []}
                )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ("." * node.level)
            for alias in node.names:
                symbols.append(
                    {
                        "kind": "import",
                        "name": f"{module}.{alias.name}",
                        "line": node.lineno,
                        "bases": [],
                    }
                )

    return symbols


class CodeSymbolsTool(Tool):
    name: ClassVar[str] = "CodeSymbols"
    description: ClassVar[str] = (
        "List top-level classes, functions, methods, and imports in a Python "
        "file with line numbers, via `ast` (no execution). Read-only; prefer "
        "this over Bash/grep for a structural overview of a module."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Absolute path to a .py/.pyi file."},
            "include_imports": {
                "type": "boolean",
                "description": "Include import statements (default true).",
            },
        },
        "required": ["file_path"],
    }
    category: ClassVar[PermissionCategory] = PermissionCategory.READ
    is_concurrent_safe: ClassVar[bool] = True

    async def execute(
        self,
        file_path: str = "",
        include_imports: bool = True,
        **kwargs: Any,
    ) -> ToolResult:
        p = Path(file_path)
        if not p.is_absolute():
            return ToolResult(
                output=f"Error: file_path must be absolute, got: {file_path}", error=True
            )
        if not p.is_file():
            return ToolResult(output=f"Error: not a file: {file_path}", error=True)
        if p.suffix not in _PY_EXTENSIONS:
            return ToolResult(
                output=f"Error: CodeSymbols only supports Python files (.py/.pyi), got: {p.suffix}",
                error=True,
            )

        try:
            source = p.read_text(encoding="utf-8")
        except OSError as e:
            return ToolResult(output=f"Error reading {file_path}: {e}", error=True)

        try:
            tree = ast.parse(source, filename=str(p))
        except SyntaxError as e:
            return ToolResult(output=f"Error parsing {file_path}: {e}", error=True)

        symbols = _extract_symbols(tree)
        if not include_imports:
            symbols = [s for s in symbols if s["kind"] != "import"]

        truncated = len(symbols) > _MAX_SYMBOLS
        shown = symbols[:_MAX_SYMBOLS]

        if not shown:
            return ToolResult(
                output=f"{file_path}: no symbols found.",
                metadata={"path": str(p), "symbol_count": 0, "truncated": False},
            )

        lines = [f"{file_path}:"]
        for s in shown:
            extra = f" ({', '.join(s['bases'])})" if s["bases"] else ""
            lines.append(f"  {s['line']:>5}  {s['kind']:<20} {s['name']}{extra}")
        if truncated:
            lines.append(f"  ... [truncated at {_MAX_SYMBOLS} symbols of {len(symbols)}]")

        return ToolResult(
            output="\n".join(lines),
            metadata={
                "path": str(p),
                "symbol_count": len(symbols),
                "truncated": truncated,
                "symbols": shown,
            },
        )
