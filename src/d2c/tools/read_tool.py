"""Read a file from the local filesystem.

Supports text files (cat -n style), PDFs (via pymupdf), images (base64),
and Jupyter notebooks (.ipynb). Binary files return an error.
"""

from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
from typing import Any, ClassVar

from d2c.tools import PermissionCategory, Tool, ToolResult


class FileReadTool(Tool):
    name: ClassVar[str] = "Read"
    description: ClassVar[str] = (
        "Reads a file from the local filesystem. "
        "The file_path parameter must be an absolute path, not a relative path. "
        "By default, it reads up to 2000 lines starting from the beginning of the file. "
        "Use offset and limit to read specific portions. "
        "Supports text files, PDF files, and images (PNG, JPG)."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The absolute path to the file to read.",
            },
            "offset": {
                "type": "integer",
                "description": "The line number to start reading from. Only for text files.",
            },
            "limit": {
                "type": "integer",
                "description": "The number of lines to read. Only for text files.",
            },
        },
        "required": ["file_path"],
    }
    category: ClassVar[PermissionCategory] = PermissionCategory.READ
    is_concurrent_safe: ClassVar[bool] = True

    IMAGE_EXTENSIONS: ClassVar[set[str]] = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg"}

    async def execute(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> ToolResult:
        path = Path(file_path)

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
                output=f"Error: path is a directory, not a file: {file_path}",
                error=True,
            )

        ext = path.suffix.lower()

        if ext == ".pdf":
            result = await self._read_pdf(path)
        elif ext in self.IMAGE_EXTENSIONS:
            result = await self._read_image(path)
        elif ext == ".ipynb":
            result = await self._read_notebook(path)
        else:
            result = await self._read_text(path, offset, limit)

        # Phase 34: register the file as read so Write/Edit's "must Read
        # first" guard is satisfiable, and surface any nested project memory.
        if not result.error:
            from d2c.tools.write_tool import mark_file_read
            from d2c.tools import notify_file_access
            mark_file_read(str(path))
            result = notify_file_access(path, result)

        return result

    async def _read_text(self, path: Path, offset: int, limit: int) -> ToolResult:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return ToolResult(output=f"Error reading file: {e}", error=True)

        lines = text.split("\n")

        if offset >= len(lines):
            return ToolResult(
                output=f"Error: offset {offset} exceeds file length {len(lines)}",
                error=True,
            )

        end = min(offset + limit, len(lines))
        selected = lines[offset:end]

        result_lines = []
        for i, line in enumerate(selected):
            line_num = offset + i + 1
            result_lines.append(f"{line_num}\t{line}")

        output = "\n".join(result_lines)

        if end < len(lines):
            output += f"\n\n[Showing lines {offset + 1}-{end} of {len(lines)} total]"

        return ToolResult(
            output=output,
            metadata={
                "total_lines": len(lines),
                "shown_start": offset + 1,
                "shown_end": end,
                "file_size": path.stat().st_size,
            },
        )

    async def _read_pdf(self, path: Path) -> ToolResult:
        try:
            import fitz
        except ImportError:
            return ToolResult(
                output="Error: pymupdf (fitz) is required to read PDF files. Install with: pip install pymupdf",
                error=True,
            )

        try:
            doc = fitz.open(str(path))
            pages = []
            for page_num in range(min(20, doc.page_count)):
                page = doc[page_num]
                text = page.get_text()
                if text.strip():
                    pages.append(f"--- Page {page_num + 1} ---\n{text}")

            output = "\n\n".join(pages)
            if doc.page_count > 20:
                output += f"\n\n[Showing first 20 of {doc.page_count} total pages]"

            return ToolResult(
                output=output,
                metadata={
                    "total_pages": doc.page_count,
                    "shown_pages": min(20, doc.page_count),
                    "file_size": path.stat().st_size,
                },
            )
        except Exception as e:
            return ToolResult(output=f"Error reading PDF: {e}", error=True)

    async def _read_image(self, path: Path) -> ToolResult:
        try:
            data = path.read_bytes()
            mime_type, _ = mimetypes.guess_type(str(path))
            if not mime_type:
                mime_type = "image/png"
            b64 = base64.b64encode(data).decode("ascii")
            return ToolResult(
                output=f"[Image: {path.name} ({len(data)} bytes, {mime_type})]",
                attachments=[{
                    "type": "image",
                    "mime_type": mime_type,
                    "data": b64,
                }],
                metadata={"file_size": len(data), "mime_type": mime_type},
            )
        except OSError as e:
            return ToolResult(output=f"Error reading image: {e}", error=True)

    async def _read_notebook(self, path: Path) -> ToolResult:
        try:
            nb = json.loads(path.read_text(encoding="utf-8"))
            cells = nb.get("cells", [])
            output_lines = []

            for i, cell in enumerate(cells):
                cell_type = cell.get("cell_type", "unknown")
                source = "".join(cell.get("source", []))

                if cell_type == "code":
                    output_lines.append(f"In [{i}]: {source.rstrip()}")
                    for out in cell.get("outputs", []):
                        text_parts = out.get("text", [])
                        if text_parts:
                            output_lines.append("".join(text_parts).rstrip())
                elif cell_type == "markdown":
                    output_lines.append(f"[{i} markdown]: {source.rstrip()}")
                output_lines.append("")

            return ToolResult(
                output="\n".join(output_lines),
                metadata={"total_cells": len(cells), "file_size": path.stat().st_size},
            )
        except (json.JSONDecodeError, OSError) as e:
            return ToolResult(output=f"Error reading notebook: {e}", error=True)
