"""Phase 74: approval-choice mapping for the Textual UI.

Pure logic (no Textual dependency) so it is unit-testable and shares the exact
Phase 52/64/65 semantics: [y] once, [a] session-only, [A] persistent, [n] deny.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class ApprovalChoice(Enum):
    ONCE = "once"  # [y] allow this once
    SESSION = "session"  # [a] allow for this session (in-memory only)
    ALWAYS = "always"  # [A] allow always (persisted to disk)
    DENY = "deny"  # [n] deny (default)


_BUTTON_CHOICES = {
    "deny": ApprovalChoice.DENY,
    "once": ApprovalChoice.ONCE,
    "session": ApprovalChoice.SESSION,
    "always": ApprovalChoice.ALWAYS,
}

# Button label + id + variant for the modal, in display order (Deny first so it
# is the visual default). Kept here so app.py doesn't re-encode the mapping.
APPROVAL_BUTTONS = (
    ("Deny", "deny", "error"),
    ("Once", "once", "default"),
    ("Session", "session", "primary"),
    ("Always", "always", "warning"),
)


def choice_from_button(button_id: str | None) -> ApprovalChoice:
    """Map a modal button id to a choice. Anything unknown denies."""
    return _BUTTON_CHOICES.get(button_id or "", ApprovalChoice.DENY)


def choice_from_key(key: str) -> ApprovalChoice:
    """Map a keypress/word to a choice. Case-sensitive for a/A (Phase 65):
    lowercase 'a' is session, uppercase 'A' is persistent. Anything
    unrecognized (including empty) denies."""
    if key == "A" or key.lower() == "always":
        return ApprovalChoice.ALWAYS
    low = key.lower()
    if low in ("y", "yes"):
        return ApprovalChoice.ONCE
    if low in ("a", "session"):
        return ApprovalChoice.SESSION
    return ApprovalChoice.DENY


def approval_view(request: Any, result: Any) -> dict[str, Any]:
    """Structured, redacted data for the approval modal — tool, category,
    reason, an input preview, an optional Bash risk verdict, and an optional
    diff summary/lines for file-changing tools.

    Reuses main's Phase 65 helpers (``_bash_risk_verdict``, ``_diff_preview``,
    ``_tool_input_preview``) so semantics/redaction aren't duplicated. Never
    reads files, never shows secrets or persistent hashes.
    """
    from d2c import main as _m
    from d2c.observability import redact

    tool_name = getattr(request, "tool_name", "")
    raw_input = getattr(request, "tool_input", None)
    tool_input = raw_input if isinstance(raw_input, dict) else {}
    category = getattr(getattr(request, "tool_category", None), "value", None) or "unknown"
    reason = getattr(result, "reason", "") or "approval required"

    view: dict[str, Any] = {
        "tool": tool_name,
        "category": category,
        "reason": reason,
        "preview": "",
        "risk": None,
        "diff_summary": "",
        "diff_lines": [],
    }

    if tool_name == "Bash":
        command = str(tool_input.get("command", ""))
        view["preview"] = str(redact(command))
        view["risk"] = _m._bash_risk_verdict(command)
    elif tool_name in ("Edit", "Write", "ApplyPatch"):
        summary, lines = _m._diff_preview(tool_name, tool_input)
        view["diff_summary"] = summary
        view["diff_lines"] = lines
        view["preview"] = str(tool_input.get("file_path") or tool_input.get("path") or "")
    elif tool_name == "WebFetch":
        view["preview"] = str(redact(tool_input.get("url", "")))
    elif tool_name == "WebSearch":
        view["preview"] = str(redact(tool_input.get("query", "")))
    else:
        view["preview"] = _m._tool_input_preview(tool_input)

    return view


def apply_choice(choice: ApprovalChoice, cache: Any, request: Any) -> bool:
    """Apply a choice against the ApprovalCache, returning whether the action is
    approved. SESSION caches in-memory only; ALWAYS persists; ONCE approves
    without caching; DENY rejects."""
    if choice is ApprovalChoice.DENY:
        return False
    if choice is ApprovalChoice.SESSION:
        cache.approve(request, persist=False)
    elif choice is ApprovalChoice.ALWAYS:
        cache.approve(request)  # persist=True (default) → write-through to disk
    return True
