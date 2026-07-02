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
