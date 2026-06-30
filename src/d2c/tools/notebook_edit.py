"""Edit Jupyter notebook cells. Paper Section 3.2 — NotebookEdit tool.

Parse .ipynb JSON, modify/add/delete cells, and write back.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

from d2c.tools import PermissionCategory, Tool, ToolResult


class NotebookEditTool(Tool):
    name: ClassVar[str] = "NotebookEdit"
    description: ClassVar[str] = (
        "Edit cells in a Jupyter notebook (.ipynb file). "
        "Can read, modify, add, or delete cells. "
        "Returns the notebook content with cell outputs included."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "notebook_path": {
                "type": "string",
                "description": "Path to the .ipynb notebook file.",
            },
            "action": {
                "type": "string",
                "enum": ["read", "edit", "add", "delete"],
                "description": "Action to perform: read cells, edit a cell, add a cell, or delete a cell.",
            },
            "cell_id": {
                "type": "integer",
                "description": "Index of the cell to edit/delete (0-based). Required for edit, delete.",
            },
            "new_source": {
                "type": "string",
                "description": "New source code for the cell. Required for edit, add.",
            },
            "cell_type": {
                "type": "string",
                "enum": ["code", "markdown", "raw"],
                "description": "Type of cell to add. Defaults to 'code'.",
            },
        },
        "required": ["notebook_path", "action"],
    }
    category: ClassVar[PermissionCategory] = PermissionCategory.WRITE
    is_concurrent_safe: ClassVar[bool] = False

    def __init__(self, cwd: Path | None = None):
        self._cwd = cwd or Path.cwd()

    async def execute(
        self,
        notebook_path: str,
        action: str,
        cell_id: int | None = None,
        new_source: str | None = None,
        cell_type: str = "code",
    ) -> ToolResult:
        nb_path = Path(notebook_path)
        if not nb_path.is_absolute():
            nb_path = self._cwd / notebook_path
        nb_path = nb_path.resolve()

        # Load notebook
        try:
            nb_data = json.loads(nb_path.read_text(encoding="utf-8"))
        except OSError as e:
            return ToolResult(output=f"Error reading notebook: {e}", error=True)
        except json.JSONDecodeError as e:
            return ToolResult(output=f"Invalid JSON in notebook: {e}", error=True)

        if "cells" not in nb_data:
            return ToolResult(output="Invalid notebook: missing 'cells' key.", error=True)

        cells = nb_data["cells"]

        if action == "read":
            return self._read_cells(cells)

        elif action == "edit":
            result = self._edit_cell(cells, cell_id, new_source)
            if result.error:
                return result

        elif action == "add":
            result = self._add_cell(cells, new_source, cell_type)

        elif action == "delete":
            result = self._delete_cell(cells, cell_id)
            if result.error:
                return result

        else:
            return ToolResult(
                output=f"Unknown action: {action}. Use: read, edit, add, delete.",
                error=True,
            )

        # Write back
        try:
            nb_path.write_text(json.dumps(nb_data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        except OSError as e:
            return ToolResult(output=f"Error writing notebook: {e}", error=True)

        return result

    def _read_cells(self, cells: list[dict]) -> ToolResult:
        """Return a readable summary of all cells."""
        lines: list[str] = []
        for i, cell in enumerate(cells):
            ctype = cell.get("cell_type", "unknown")
            source = "".join(cell.get("source", []))
            source_preview = source[:100] + ("..." if len(source) > 100 else "")
            lines.append(f"[{i}] {ctype}: {source_preview}")

        return ToolResult(
            output="\n".join(lines),
            metadata={"cell_count": len(cells)},
        )

    def _edit_cell(
        self, cells: list[dict], cell_id: int | None, new_source: str | None,
    ) -> ToolResult:
        if cell_id is None:
            return ToolResult(output="cell_id is required for edit action.", error=True)
        if new_source is None:
            return ToolResult(output="new_source is required for edit action.", error=True)
        if cell_id < 0 or cell_id >= len(cells):
            return ToolResult(
                output=f"Invalid cell_id: {cell_id}. Notebook has {len(cells)} cells (0-{len(cells) - 1}).",
                error=True,
            )

        old_source = "".join(cells[cell_id].get("source", []))
        cells[cell_id]["source"] = new_source.split("\n")
        return ToolResult(
            output=f"Cell [{cell_id}] updated.\nOld: {old_source[:100]}\nNew: {new_source[:100]}",
            metadata={"cell_id": cell_id, "action": "edit"},
        )

    def _add_cell(
        self, cells: list[dict], new_source: str | None, cell_type: str,
    ) -> ToolResult:
        if new_source is None:
            return ToolResult(output="new_source is required for add action.", error=True)

        new_cell = {
            "cell_type": cell_type,
            "metadata": {},
            "source": new_source.split("\n"),
        }
        if cell_type == "code":
            new_cell["outputs"] = []
            new_cell["execution_count"] = None

        cells.append(new_cell)
        return ToolResult(
            output=f"Cell [{len(cells) - 1}] added ({cell_type}).",
            metadata={"cell_id": len(cells) - 1, "action": "add", "cell_type": cell_type},
        )

    def _delete_cell(self, cells: list[dict], cell_id: int | None) -> ToolResult:
        if cell_id is None:
            return ToolResult(output="cell_id is required for delete action.", error=True)
        if cell_id < 0 or cell_id >= len(cells):
            return ToolResult(
                output=f"Invalid cell_id: {cell_id}. Notebook has {len(cells)} cells (0-{len(cells) - 1}).",
                error=True,
            )

        deleted = cells.pop(cell_id)
        ctype = deleted.get("cell_type", "unknown")
        source = "".join(deleted.get("source", []))
        return ToolResult(
            output=f"Cell [{cell_id}] ({ctype}) deleted: {source[:100]}",
            metadata={"cell_id": cell_id, "action": "delete"},
        )
