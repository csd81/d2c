"""Phase 74: Markdown rendering for the Textual UI, via Rich.

Display-only and fail-open: returns a Rich ``Markdown`` renderable when Rich is
available, otherwise the plain string. It never executes HTML, fetches links, or
reads files — the same safety contract as the prompt_toolkit renderer
(``d2c.markdown_render``).
"""

from __future__ import annotations

from typing import Any


def to_renderable(text: str) -> Any:
    """A Rich ``Markdown`` renderable for ``text``, or the plain string on any
    import/parse error."""
    try:
        from rich.markdown import Markdown

        return Markdown(text)
    except Exception:
        return text
