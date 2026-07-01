"""Project Workspace Trust Gate.

Prevents eager loading of project-local files (.env, plugins, skills,
MCP configs, CLAUDE.md) in untrusted workspaces. Uses a persistent
trust store at ~/.d2c/trusted.json with ancestor-path matching.

Pattern: module-level singleton matching get_background_manager()
in subagent.py and get_file_history_tracker() in tools/__init__.py.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

logger = logging.getLogger(__name__)


# ── Data types ─────────────────────────────────────────────────────────


@dataclass
class TrustEntry:
    """A single trusted project path with metadata."""

    path: str  # resolved absolute path (plain text, debuggable)
    added: str  # ISO-8601 timestamp
    last_used: str  # ISO-8601 timestamp


# ── Persistent store ───────────────────────────────────────────────────


class TrustStore:
    """Persistent trust store at ~/.d2c/trusted.json.

    Format:
      {
        "version": 1,
        "trusted": [
          {"path": "/home/alice/projects/myapp", "added": "...", "last_used": "..."}
        ]
      }

    Ancestor matching: trusting /home/alice/projects also trusts
    /home/alice/projects/myapp and all subdirectories.
    """

    PATH: ClassVar[Path] = Path.home() / ".d2c" / "trusted.json"
    CURRENT_VERSION: ClassVar[int] = 1

    def __init__(self) -> None:
        self._entries: dict[str, TrustEntry] = {}
        self._load()

    # ── Public API ──────────────────────────────────────────────────

    def is_trusted(self, project_path: Path) -> bool:
        """Return True if the resolved path or any ancestor is trusted.

        Ancestor matching means trusting ~/projects automatically trusts
        ~/projects/repo-a, ~/projects/repo-b, etc.
        """
        resolved = project_path.resolve()
        parts = resolved.parts

        # Check exact match and all ancestor paths
        for i in range(len(parts), 0, -1):
            ancestor = str(Path(*parts[:i]))
            if ancestor in self._entries:
                return True

        return False

    def trust(self, project_path: Path) -> None:
        """Add or update a trusted project path. Persists immediately."""
        resolved = str(project_path.resolve())
        now = _utc_now_iso()

        if resolved in self._entries:
            self._entries[resolved].last_used = now
        else:
            self._entries[resolved] = TrustEntry(
                path=resolved,
                added=now,
                last_used=now,
            )
        self._save()

    def untrust(self, project_path: Path) -> None:
        """Remove a project path from the trust store. Persists immediately."""
        resolved = str(project_path.resolve())
        if resolved in self._entries:
            del self._entries[resolved]
            self._save()

    def list_trusted(self) -> list[TrustEntry]:
        """Return all trusted entries, sorted by path."""
        return sorted(self._entries.values(), key=lambda e: e.path)

    # ── Persistence ─────────────────────────────────────────────────

    def _load(self) -> None:
        """Load trusted paths from disk. Graceful on missing/corrupt file."""
        if not self.PATH.exists():
            return

        try:
            raw = json.loads(self.PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Trust store corrupt, starting fresh: %s", e)
            return

        if not isinstance(raw, dict):
            return

        version = raw.get("version", 0)
        if version != self.CURRENT_VERSION:
            # Future: handle version migration
            pass

        for entry_data in raw.get("trusted", []):
            if isinstance(entry_data, dict) and "path" in entry_data:
                path = entry_data["path"]
                self._entries[path] = TrustEntry(
                    path=path,
                    added=entry_data.get("added", ""),
                    last_used=entry_data.get("last_used", ""),
                )

    def _save(self) -> None:
        """Persist trusted paths to disk atomically."""
        self.PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": self.CURRENT_VERSION,
            "trusted": [
                {"path": e.path, "added": e.added, "last_used": e.last_used}
                for e in sorted(self._entries.values(), key=lambda x: x.path)
            ],
        }
        tmp_path = self.PATH.with_suffix(".tmp")
        try:
            tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp_path.replace(self.PATH)
        except OSError as e:
            logger.warning("Failed to save trust store: %s", e)


# ── Session gate ───────────────────────────────────────────────────────


class WorkSpaceTrustGate:
    """Holds the trust decision for the current CLI session.

    Created once at startup in main(), stored globally, and queried by
    every loader that touches project-local files.

    Must call decide() before accessing is_project_trusted.
    """

    def __init__(self, cwd: Path, trust_store: TrustStore | None = None) -> None:
        self._cwd = cwd.resolve()
        self._store = trust_store or TrustStore()
        self._decided: bool = False
        self._trusted: bool = False

    @property
    def is_project_trusted(self) -> bool:
        """True if project-local resources should be loaded.

        All loaders call this. Must only be accessed after decide().
        """
        if not self._decided:
            raise RuntimeError(
                "Trust decision not made yet. Call decide() before querying is_project_trusted."
            )
        return self._trusted

    @property
    def decided(self) -> bool:
        """True if decide() has been called."""
        return self._decided

    def decide(self, trust: bool) -> None:
        """Set the session trust decision. Idempotent — second call is a no-op."""
        if not self._decided:
            self._decided = True
            self._trusted = trust

    def prompt_trust(self) -> bool:
        """Synchronous interactive trust dialog.

        Returns True if user says yes, False otherwise.
        Made synchronous because input() is sync and main() is not async.
        """
        try:
            answer = input("Trust this workspace? [y/N] ").strip().lower()
            return answer in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            print()
            return False


# ── Module singleton ───────────────────────────────────────────────────

_trust_gate: WorkSpaceTrustGate | None = None


def get_trust_gate() -> WorkSpaceTrustGate:
    """Return the session trust gate.

    If not explicitly initialized (programmatic use, tests), returns a
    default gate that trusts the workspace — backward compatible behavior.
    When main() is the entry point, _resolve_trust() always calls
    set_trust_gate() so this fallback is never reached in production.
    """
    global _trust_gate
    if _trust_gate is None:
        _trust_gate = WorkSpaceTrustGate(Path.cwd())
        _trust_gate.decide(True)  # default: trust (backward compatible)
    return _trust_gate


def set_trust_gate(gate: WorkSpaceTrustGate) -> None:
    """Set the global session trust gate. Called once at startup."""
    global _trust_gate
    _trust_gate = gate


def reset_trust_gate() -> None:
    """Reset the global trust gate (for testing)."""
    global _trust_gate
    _trust_gate = None


# ── Helpers ────────────────────────────────────────────────────────────


def _utc_now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()
