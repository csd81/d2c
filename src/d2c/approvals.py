"""Approval cache (Phase 52), with optional cross-session/restart persistence
(Phase 64).

In-memory only by default. When the user answers ``a`` ("always allow this
exact action for this session") to an ASK prompt, the exact action is cached
so identical repeats don't re-prompt. The cache stores only SHA-256 hashes of
the action (never raw tool input).

Phase 64: passing a ``path`` opts into a JSON file (default
``~/.d2c/approvals.json``) of ``{sha256_hash: iso_timestamp}`` pairs — never
raw tool input or command text, so a process reading the file cannot
reconstruct the original commands. The file lives in ``~/.d2c/``, which
already stores trusted user-level data (``trusted.json``, sessions); no new
trust boundary applies. Loaded once at construction; ``approve()``
write-throughs immediately (the write is triggered by a single "a" keypress,
not per tool call, so this is cheap). A corrupted or unreadable file never
raises — the cache just starts empty and logs a warning.

``clear()`` (called on session switch — ``/clear``/``/resume``/``/fork``)
only empties the in-memory set; it never touches disk, so a session switch
resets what *this* session trusts without forgetting approvals persisted for
future sessions or process restarts. ``reset()`` empties the in-memory set
*and* deletes the persisted file — the explicit "forget everything" action.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_APPROVALS_PATH = Path.home() / ".d2c" / "approvals.json"


class ApprovalCache:
    """Conservative, exact-match approval cache keyed by a hash of
    (tool name, permission category, normalized tool input).

    ``path=None`` (default) keeps the cache purely in-memory — no disk I/O,
    the pre-Phase-64 behavior. Pass an explicit path (or the
    ``DEFAULT_APPROVALS_PATH`` constant) to load existing approvals at
    construction and persist new ones as they're approved.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._keys: dict[str, str] = {}  # sha256 hash -> ISO timestamp
        self._path = path
        self._lock = threading.Lock()
        if self._path is not None:
            self.load()

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

    def approve(self, request: Any, *, persist: bool = True) -> None:
        """Approve an exact action for the runtime session.

        Phase 65: ``persist=False`` (the REPL's "session" scope, [a]) adds
        the action to the in-memory set only — it behaves exactly like the
        Phase 52 in-memory-only cache and disappears on ``clear()``/restart,
        even if this cache has a disk path. ``persist=True`` (the default,
        and the REPL's "always" scope, [A]) additionally writes through to
        disk when a path is configured, so it survives ``clear()`` and
        process restarts (Phase 64).
        """
        self._keys[self._key(request)] = datetime.now(timezone.utc).isoformat()
        if persist and self._path is not None:
            self.save()

    def clear(self) -> None:
        """Empty the in-memory (runtime) approval set. Never touches disk."""
        self._keys.clear()

    def clear_session(self) -> int:
        """Drop in-memory approvals that are NOT persisted to disk (the "a" /
        session scope). Persistent approvals stay active for this session and
        untouched on disk. Returns the number of session approvals removed.

        With no disk path, every in-memory approval is session-scoped, so this
        behaves like :meth:`clear` and returns the count cleared.
        """
        persisted = self._persisted_keys()
        session_keys = [k for k in self._keys if k not in persisted]
        for k in session_keys:
            del self._keys[k]
        return len(session_keys)

    def reset(self) -> None:
        """Empty the runtime set AND delete the persisted file, if any."""
        self._keys.clear()
        if self._path is not None and self._path.exists():
            try:
                self._path.unlink()
            except OSError as e:
                logger.warning("Failed to delete approvals file %s: %s", self._path, e)

    def __len__(self) -> int:
        return len(self._keys)

    # ── Introspection (Phase 70) ────────────────────────────────────

    def path(self) -> Path | None:
        """The persistence file path, or None for an in-memory-only cache."""
        return self._path

    def runtime_count(self) -> int:
        """Total in-memory approvals (session-only plus persisted-and-loaded)."""
        return len(self._keys)

    def _persisted_keys(self) -> set[str]:
        """Hashes currently on disk. Best-effort and never raises: a missing,
        unreadable, or corrupted file counts as no persisted approvals."""
        if self._path is None or not self._path.exists():
            return set()
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return set()
        if not isinstance(data, dict):
            return set()
        return {k for k in data if isinstance(k, str) and len(k) == 64}

    def persistent_count(self) -> int:
        """Number of approvals persisted on disk (i.e. surviving a restart)."""
        return len(self._persisted_keys())

    def session_count(self) -> int:
        """In-memory approvals that are not persisted to disk (the "a" scope)."""
        persisted = self._persisted_keys()
        return sum(1 for k in self._keys if k not in persisted)

    # ── Persistence (Phase 64) ──────────────────────────────────────

    def load(self) -> None:
        """Load {hash: timestamp} pairs from disk into the runtime set.

        Never raises: a missing, unreadable, or corrupted file just means
        starting empty (with a warning logged for the latter two).
        """
        if self._path is None or not self._path.exists():
            return
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning("Failed to read approvals file %s: %s", self._path, e)
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning("Approvals file %s is corrupted (%s); starting empty", self._path, e)
            return
        if not isinstance(data, dict):
            logger.warning("Approvals file %s is not a JSON object; starting empty", self._path)
            return
        for k, v in data.items():
            if isinstance(k, str) and len(k) == 64:
                self._keys[k] = v if isinstance(v, str) else datetime.now(timezone.utc).isoformat()

    def save(self) -> None:
        """Persist the current {hash: timestamp} set to disk atomically
        (write a sibling .tmp file, then os.replace over the target)."""
        if self._path is None:
            return
        with self._lock:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
                tmp_path.write_text(
                    json.dumps(self._keys, indent=2, sort_keys=True), encoding="utf-8"
                )
                os.replace(tmp_path, self._path)
            except OSError as e:
                logger.warning("Failed to save approvals to %s: %s", self._path, e)
