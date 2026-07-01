"""Session-scoped approval cache (Phase 52).

In-memory only. When the user answers ``a`` ("always allow this exact action
for this session") to an ASK prompt, the exact action is cached so identical
repeats don't re-prompt. The cache stores only SHA-256 hashes of the action
(never raw tool input), is never persisted, and is cleared on session switch
(/clear, /resume, /fork) and process restart.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


class ApprovalCache:
    """Conservative, exact-match approval cache keyed by a hash of
    (tool name, permission category, normalized tool input)."""

    def __init__(self) -> None:
        self._keys: set[str] = set()

    @staticmethod
    def _key(request: Any) -> str:
        payload = {
            "tool": getattr(request, "tool_name", None),
            "category": getattr(
                getattr(request, "tool_category", None),
                "value",
                str(getattr(request, "tool_category", None)),
            ),
            # exact input — for Bash this includes the full command string
            "input": getattr(request, "tool_input", None),
        }
        blob = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def is_approved(self, request: Any) -> bool:
        return self._key(request) in self._keys

    def approve(self, request: Any) -> None:
        self._keys.add(self._key(request))

    def clear(self) -> None:
        self._keys.clear()

    def __len__(self) -> int:
        return len(self._keys)
