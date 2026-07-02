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
