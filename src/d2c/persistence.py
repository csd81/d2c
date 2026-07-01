"""Append-only JSONL session transcripts + resume/fork. Paper Section 9.

Key properties:
- Every event is append-only (except compaction cleanup rewrites)
- Human-readable, version-controllable, reconstructable
- Resume and fork restore messages but NOT permissions (paper Section 9.2)
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

SESSION_DIR_NAME = "sessions"


# ── Session entry ─────────────────────────────────────────────────────


@dataclass
class SessionEntry:
    """One line in the JSONL transcript."""

    role: str  # "user" | "assistant" | "tool" | "system"
    content: str | list[dict]  # text or content blocks
    timestamp: str  # ISO 8601 UTC
    entry_type: str = "message"  # "message" | "compact_boundary" | "subagent_summary"
    metadata: dict = field(default_factory=dict)

    def to_jsonl_line(self) -> str:
        return json.dumps(
            {
                "role": self.role,
                "content": self.content,
                "timestamp": self.timestamp,
                "entry_type": self.entry_type,
                "metadata": self.metadata,
            }
        )


# ── Session store ─────────────────────────────────────────────────────


class SessionStore:
    """Append-only JSONL session transcript (paper Section 9)."""

    def __init__(self, base_dir: Path, session_id: str, project_dir: Path):
        self.base_dir = base_dir
        self.session_id = session_id
        self.project_dir = project_dir
        sessions_dir = base_dir / SESSION_DIR_NAME
        self.transcript_path = sessions_dir / f"{session_id}.jsonl"
        self.manifest_path = sessions_dir / f"{session_id}.manifest.json"
        self.sidechain_dir = sessions_dir / f"{session_id}_sidechains"

    # --- Write ---

    def append(self, entry: SessionEntry) -> None:
        """Append one line to transcript. Creates dirs if needed."""
        self.transcript_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.transcript_path, "a", encoding="utf-8") as f:
            f.write(entry.to_jsonl_line() + "\n")

    def append_compact_boundary(self, preserved_head_uuid: str) -> None:
        """Paper Section 9: compact_boundary marker for resume reconstruction."""
        entry = SessionEntry(
            role="system",
            content="",
            timestamp=_utc_now(),
            entry_type="compact_boundary",
            metadata={"preserved_head_uuid": preserved_head_uuid},
        )
        self.append(entry)

    # --- Read ---

    def read_transcript(self) -> list[SessionEntry]:
        """Read all entries for resume/replay."""
        if not self.transcript_path.exists():
            return []
        entries = []
        with open(self.transcript_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    entries.append(
                        SessionEntry(
                            role=data.get("role", "unknown"),
                            content=data.get("content", ""),
                            timestamp=data.get("timestamp", ""),
                            entry_type=data.get("entry_type", "message"),
                            metadata=data.get("metadata", {}),
                        )
                    )
                except json.JSONDecodeError:
                    logger.warning("Skipping malformed JSONL line in %s", self.transcript_path)
        return entries

    def reconstruct_messages(self, entries: list[SessionEntry] | None = None) -> list[dict]:
        """Rebuild message array from transcript entries.

        Paper: compact_boundary entries mark where compaction occurred;
        the post-compact messages replace the pre-compact ones.
        """
        if entries is None:
            entries = self.read_transcript()

        messages: list[dict] = []

        for entry in entries:
            if entry.entry_type == "compact_boundary":
                # Reset — pre-compact content discarded, post-compact follows
                messages = []
                continue
            if entry.entry_type == "message":
                # Skip system meta entries (session_start, forked_from, session_stop)
                if entry.role == "system":
                    continue
                if entry.role == "tool":
                    messages.append(
                        {
                            "role": "tool",
                            "content": entry.content,
                            "tool_use_id": entry.metadata.get("tool_use_id", ""),
                            "tool_name": entry.metadata.get("tool_name", ""),
                        }
                    )
                else:
                    messages.append(
                        {
                            "role": entry.role,
                            "content": entry.content,
                        }
                    )
        return messages

    # --- Sidechains (Phase 8) ---

    def get_sidechain_path(self, subagent_id: str) -> Path:
        """Paper Section 8.3: sidechain transcripts for subagents."""
        self.sidechain_dir.mkdir(parents=True, exist_ok=True)
        return self.sidechain_dir / f"{subagent_id}.jsonl"


# ── Session manager ───────────────────────────────────────────────────


class SessionManager:
    """Session lifecyle: create, resume, fork (paper Section 9.1-9.2)."""

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = base_dir or Path.home() / ".d2c"

    def create_session(self, cwd: Path) -> SessionStore:
        """Create a new session with a unique ID."""
        session_id = str(uuid.uuid4())[:8]
        store = SessionStore(self.base_dir, session_id, cwd)
        store.append(
            SessionEntry(
                role="system",
                content="",
                timestamp=_utc_now(),
                entry_type="message",
                metadata={"event": "session_start", "cwd": str(cwd)},
            )
        )
        return store

    def resume_session(self, session_id: str, cwd: Path) -> tuple[SessionStore, list[dict]]:
        """Resume from an existing session transcript.

        IMPORTANT: Session-scoped permissions are NOT restored (paper Section 9.2).
        Returns (store, messages) where messages is the reconstructed conversation.
        """
        store = SessionStore(self.base_dir, session_id, cwd)
        if not store.transcript_path.exists():
            raise FileNotFoundError(f"Session not found: {session_id}")
        entries = store.read_transcript()
        messages = store.reconstruct_messages(entries)
        return store, messages

    def fork_session(self, source_id: str, cwd: Path) -> SessionStore:
        """Fork: copy transcript from an existing session into a new one (paper Section 9.2)."""
        source_store = SessionStore(self.base_dir, source_id, cwd)
        if not source_store.transcript_path.exists():
            raise FileNotFoundError(f"Source session not found: {source_id}")

        new_store = self.create_session(cwd)

        # Copy message entries from source (skip compact_boundary markers)
        for entry in source_store.read_transcript():
            if entry.entry_type == "compact_boundary":
                continue
            new_store.append(entry)

        new_store.append(
            SessionEntry(
                role="system",
                content="",
                timestamp=_utc_now(),
                entry_type="message",
                metadata={"event": "forked_from", "source_session": source_id},
            )
        )
        return new_store


# ── Helpers ───────────────────────────────────────────────────────────


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
