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


def tool_row_from_event(event: Any) -> str:
    """Build a timeline row from a completed ToolExecutionEvent."""
    name = getattr(getattr(event, "tool_use", None), "name", "?")
    tool_input = getattr(getattr(event, "tool_use", None), "input", {}) or {}
    result = getattr(event, "result", None)
    output = str(getattr(result, "output", "") or "")
    errored = bool(getattr(result, "error", False))
    metadata = getattr(result, "metadata", {}) or {}

    if errored:
        low = output.lower()
        status = "denied" if ("denied" in low or "not permitted" in low) else "error"
        first_line = output.strip().splitlines()[0] if output.strip() else ""
        detail = first_line[:60]
    else:
        status = "ok"
        detail = ""
        if isinstance(metadata, dict) and metadata.get("file_count"):
            detail = f"{metadata['file_count']} file(s)"

    return tool_row(
        name=str(name),
        target=tool_target_preview(str(name), tool_input),
        status=status,
        detail=detail,
    )
