"""Phase 74: small pure helpers for Textual widgets (no Textual dependency)."""

from __future__ import annotations

from typing import Any


def status_line(
    *,
    model: str,
    mode: str,
    trust: Any,
    cwd: Any,
    usage: str = "",
    bg_tasks: int = 0,
) -> str:
    """Compose the footer status text: model | mode | trust | cwd | usage | tasks."""
    parts = [
        f"model: {model}",
        f"mode: {mode}",
        f"trust: {trust}",
        f"cwd: {cwd}",
    ]
    if usage:
        parts.append(f"usage: {usage}")
    parts.append(f"tasks: {bg_tasks}")
    return "  |  ".join(parts)


# ── Phase 75: tool progress timeline rows ───────────────────────────

_PREVIEW_KEYS = ("file_path", "path", "command", "url", "query", "pattern")


def tool_target_preview(name: str, tool_input: Any) -> str:
    """A short, redacted target/input preview for a tool row (never reads
    files). Picks the most meaningful field, else a compact JSON dump."""
    from d2c.observability import redact

    if not isinstance(tool_input, dict):
        return ""
    for key in _PREVIEW_KEYS:
        value = tool_input.get(key)
        if value:
            return str(redact(value))[:60]
    import json

    try:
        blob = json.dumps(redact(tool_input), default=str)
    except Exception:
        blob = str(redact(tool_input))
    return blob[:60]


def tool_row(*, name: str, target: str, status: str, detail: str = "") -> str:
    """A compact timeline row: name, target preview, status (running/ok/error/
    denied), and an optional detail (duration, diff, or error summary)."""
    row = f"{name:<11}{target:<40} {status}"
    if detail:
        row += f"  {detail}"
    return row


def tool_row_status(event: Any) -> str:
    """Status bucket for a completed ToolExecutionEvent: ok / error / denied."""
    result = getattr(event, "result", None)
    if not bool(getattr(result, "error", False)):
        return "ok"
    low = str(getattr(result, "output", "") or "").lower()
    return "denied" if ("denied" in low or "not permitted" in low) else "error"


def tool_row_from_event(event: Any) -> str:
    """Build a timeline row from a completed ToolExecutionEvent."""
    name = getattr(getattr(event, "tool_use", None), "name", "?")
    tool_input = getattr(getattr(event, "tool_use", None), "input", {}) or {}
    result = getattr(event, "result", None)
    output = str(getattr(result, "output", "") or "")
    metadata = getattr(result, "metadata", {}) or {}
    status = tool_row_status(event)

    if status == "ok":
        detail = ""
        if isinstance(metadata, dict) and metadata.get("file_count"):
            detail = f"{metadata['file_count']} file(s)"
    else:
        first_line = output.strip().splitlines()[0] if output.strip() else ""
        detail = first_line[:60]

    return tool_row(
        name=str(name),
        target=tool_target_preview(str(name), tool_input),
        status=status,
        detail=detail,
    )


# ── Phase 76: prompt input history ──────────────────────────────────


class InputHistory:
    """Small, Textual-free prompt history for the Textual input box.

    ``add`` records a submitted prompt (deduping consecutive repeats) and
    resets the navigation cursor. ``prev``/``next`` walk the history like a
    shell: ``prev`` returns older entries, ``next`` returns newer ones and
    finally an empty string when walked past the newest. Navigation is only
    "active" once ``prev`` has been called.
    """

    def __init__(self) -> None:
        self._items: list[str] = []
        self._pos: int | None = None

    @property
    def navigating(self) -> bool:
        return self._pos is not None

    def add(self, text: str) -> None:
        text = text.strip()
        if text and (not self._items or self._items[-1] != text):
            self._items.append(text)
        self._pos = None

    def prev(self) -> str | None:
        """The previous (older) entry, or None if there is no history."""
        if not self._items:
            return None
        if self._pos is None:
            self._pos = len(self._items) - 1
        elif self._pos > 0:
            self._pos -= 1
        return self._items[self._pos]

    def next(self) -> str | None:
        """The next (newer) entry, an empty string past the newest, or None if
        not currently navigating."""
        if self._pos is None:
            return None
        if self._pos < len(self._items) - 1:
            self._pos += 1
            return self._items[self._pos]
        self._pos = None
        return ""

    def reset(self) -> None:
        self._pos = None
