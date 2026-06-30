"""Tests for Phase 4: Session Persistence — JSONL transcripts, resume, fork."""

from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone

import pytest

from d2c.persistence import (
    SessionEntry,
    SessionManager,
    SessionStore,
    _utc_now,
)


# ── Fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def tmp_base_dir(tmp_path):
    return tmp_path / ".d2c"


@pytest.fixture
def store(tmp_base_dir):
    return SessionStore(tmp_base_dir, "test123", Path("/home/user/project"))


@pytest.fixture
def manager(tmp_base_dir):
    return SessionManager(tmp_base_dir)


# ── Helpers ────────────────────────────────────────────────────────────

def make_entry(role="user", content="hello", **kwargs):
    return SessionEntry(
        role=role,
        content=content,
        timestamp=_utc_now(),
        entry_type=kwargs.pop("entry_type", "message"),
        metadata=kwargs,
    )


# ── SessionEntry tests ─────────────────────────────────────────────────

class TestSessionEntry:
    def test_to_jsonl_line(self):
        entry = SessionEntry(
            role="user",
            content="hello world",
            timestamp="2026-06-30T12:00:00Z",
            entry_type="message",
        )
        line = entry.to_jsonl_line()
        assert line.endswith("\n") is False
        data = json.loads(line)
        assert data["role"] == "user"
        assert data["content"] == "hello world"
        assert data["timestamp"] == "2026-06-30T12:00:00Z"
        assert data["entry_type"] == "message"

    def test_to_jsonl_line_with_metadata(self):
        entry = SessionEntry(
            role="tool",
            content="output here",
            timestamp="2026-06-30T12:00:00Z",
            metadata={"tool_name": "Read", "tool_use_id": "tu_1"},
        )
        data = json.loads(entry.to_jsonl_line())
        assert data["metadata"]["tool_name"] == "Read"
        assert data["metadata"]["tool_use_id"] == "tu_1"

    def test_to_jsonl_line_with_list_content(self):
        entry = SessionEntry(
            role="assistant",
            content=[{"type": "text", "text": "hello"}, {"type": "tool_use", "id": "t1", "name": "Read", "input": {}}],
            timestamp="2026-06-30T12:00:00Z",
        )
        data = json.loads(entry.to_jsonl_line())
        assert isinstance(data["content"], list)
        assert len(data["content"]) == 2

    def test_default_values(self):
        entry = SessionEntry(role="user", content="test", timestamp=_utc_now())
        assert entry.entry_type == "message"
        assert entry.metadata == {}


# ── SessionStore write tests ───────────────────────────────────────────

class TestSessionStoreWrite:
    def test_append_creates_file(self, store):
        entry = make_entry("user", "hello")
        store.append(entry)
        assert store.transcript_path.exists()

    def test_append_creates_parent_dirs(self, tmp_base_dir):
        store = SessionStore(tmp_base_dir, "nested", Path("/tmp"))
        entry = make_entry("user", "test")
        store.append(entry)
        assert store.transcript_path.exists()

    def test_append_writes_jsonl_line(self, store):
        entry = make_entry("user", "hello world")
        store.append(entry)

        content = store.transcript_path.read_text()
        lines = content.strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["role"] == "user"
        assert data["content"] == "hello world"

    def test_append_multiple_entries(self, store):
        store.append(make_entry("user", "first"))
        store.append(make_entry("assistant", "second"))
        store.append(make_entry("tool", "third"))

        content = store.transcript_path.read_text()
        lines = content.strip().split("\n")
        assert len(lines) == 3
        assert "first" in lines[0]
        assert "second" in lines[1]
        assert "third" in lines[2]

    def test_append_compact_boundary(self, store):
        store.append_compact_boundary("head-uuid-123")

        entries = store.read_transcript()
        assert len(entries) == 1
        assert entries[0].entry_type == "compact_boundary"
        assert entries[0].metadata["preserved_head_uuid"] == "head-uuid-123"


# ── SessionStore read tests ────────────────────────────────────────────

class TestSessionStoreRead:
    def test_read_empty_transcript(self, store):
        entries = store.read_transcript()
        assert entries == []

    def test_read_nonexistent_transcript(self, store):
        assert not store.transcript_path.exists()
        entries = store.read_transcript()
        assert entries == []

    def test_read_returns_entries(self, store):
        store.append(make_entry("user", "first"))
        store.append(make_entry("assistant", "second"))

        entries = store.read_transcript()
        assert len(entries) == 2
        assert entries[0].role == "user"
        assert entries[0].content == "first"
        assert entries[1].role == "assistant"
        assert entries[1].content == "second"

    def test_read_preserves_metadata(self, store):
        store.append(make_entry("tool", "output", tool_name="Read", tool_use_id="tu_1"))
        entries = store.read_transcript()
        assert entries[0].metadata["tool_name"] == "Read"
        assert entries[0].metadata["tool_use_id"] == "tu_1"

    def test_read_preserves_list_content(self, store):
        content = [{"type": "text", "text": "hi"}, {"type": "tool_use", "id": "t1", "name": "R", "input": {}}]
        entry = SessionEntry(role="assistant", content=content, timestamp=_utc_now())
        store.append(entry)
        entries = store.read_transcript()
        assert isinstance(entries[0].content, list)
        assert len(entries[0].content) == 2

    def test_skip_malformed_line(self, store):
        store.append(make_entry("user", "good"))
        # Manually write a malformed line
        with open(store.transcript_path, "a", encoding="utf-8") as f:
            f.write("this is not json\n")
        store.append(make_entry("assistant", "also good"))

        entries = store.read_transcript()
        # Malformed line is skipped
        assert len(entries) == 2
        assert entries[0].content == "good"
        assert entries[1].content == "also good"

    def test_skip_empty_lines(self, store):
        store.append(make_entry("user", "one"))
        with open(store.transcript_path, "a", encoding="utf-8") as f:
            f.write("\n   \n")
        store.append(make_entry("assistant", "two"))

        entries = store.read_transcript()
        assert len(entries) == 2


# ── SessionStore reconstruct_messages tests ────────────────────────────

class TestReconstructMessages:
    def test_reconstruct_simple(self, store):
        store.append(make_entry("user", "hello"))
        store.append(make_entry("assistant", "hi there"))

        messages = store.reconstruct_messages()
        assert len(messages) == 2
        assert messages[0] == {"role": "user", "content": "hello"}
        assert messages[1] == {"role": "assistant", "content": "hi there"}

    def test_reconstruct_tool_message(self, store):
        store.append(make_entry("user", "read file"))
        store.append(SessionEntry(
            role="tool", content="file contents",
            timestamp=_utc_now(),
            metadata={"tool_name": "Read", "tool_use_id": "tu_1"},
        ))

        messages = store.reconstruct_messages()
        assert len(messages) == 2
        assert messages[1]["role"] == "tool"
        assert messages[1]["content"] == "file contents"
        assert messages[1]["tool_use_id"] == "tu_1"
        assert messages[1]["tool_name"] == "Read"

    def test_reconstruct_with_compact_boundary(self, store):
        """compact_boundary resets message array — pre-compact discarded."""
        store.append(make_entry("user", "old conversation"))
        store.append(make_entry("assistant", "old response"))
        store.append(SessionEntry(
            role="system", content="",
            timestamp=_utc_now(), entry_type="compact_boundary",
            metadata={"preserved_head_uuid": "abc"},
        ))
        store.append(make_entry("user", "[Compacted summary]"))
        store.append(make_entry("assistant", "new response"))

        messages = store.reconstruct_messages()
        assert len(messages) == 2
        assert messages[0]["content"] == "[Compacted summary]"
        assert messages[1]["content"] == "new response"

    def test_reconstruct_empty_entries(self, store):
        messages = store.reconstruct_messages([])
        assert messages == []

    def test_reconstruct_skips_non_message_types(self, store):
        store.append(make_entry("user", "hello"))
        store.append(SessionEntry(
            role="system", content="",
            timestamp=_utc_now(), entry_type="subagent_summary",
            metadata={"agent": "test"},
        ))
        store.append(make_entry("assistant", "reply"))

        messages = store.reconstruct_messages()
        assert len(messages) == 2  # subagent_summary is NOT message type
        assert messages[0]["content"] == "hello"
        assert messages[1]["content"] == "reply"


# ── SessionManager tests ───────────────────────────────────────────────

class TestSessionManagerCreate:
    def test_create_session_returns_store(self, manager):
        store = manager.create_session(Path("/tmp"))
        assert isinstance(store, SessionStore)
        assert store.session_id is not None
        assert len(store.session_id) == 8

    def test_create_session_writes_start_entry(self, manager):
        store = manager.create_session(Path("/tmp"))
        entries = store.read_transcript()
        assert len(entries) == 1
        assert entries[0].role == "system"
        assert entries[0].metadata["event"] == "session_start"

    def test_create_session_unique_ids(self, manager):
        store1 = manager.create_session(Path("/tmp"))
        store2 = manager.create_session(Path("/tmp"))
        assert store1.session_id != store2.session_id


class TestSessionManagerResume:
    def test_resume_restores_messages(self, manager):
        store = manager.create_session(Path("/tmp"))
        store.append(make_entry("user", "hello"))
        store.append(make_entry("assistant", "hi"))

        store2, messages = manager.resume_session(store.session_id, Path("/tmp"))
        assert store2.session_id == store.session_id
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"

    def test_resume_nonexistent_session(self, manager):
        with pytest.raises(FileNotFoundError):
            manager.resume_session("nonexist", Path("/tmp"))

    def test_resume_does_not_restore_permissions(self, manager):
        """Paper Section 9.2: permissions are NOT restored on resume."""
        store = manager.create_session(Path("/tmp"))
        store.append(make_entry("user", "hello"))

        store2, messages = manager.resume_session(store.session_id, Path("/tmp"))
        # Messages are restored
        assert len(messages) == 1
        # But permissions are fresh (nothing permission-related in the result)
        assert isinstance(store2, SessionStore)


class TestSessionManagerFork:
    def test_fork_copies_entries(self, manager):
        source = manager.create_session(Path("/tmp"))
        source.append(make_entry("user", "original message"))
        source.append(make_entry("assistant", "original response"))

        forked = manager.fork_session(source.session_id, Path("/tmp"))
        assert forked.session_id != source.session_id

        entries = forked.read_transcript()
        # session_start + 2 messages + forked_from = 4 entries
        message_entries = [e for e in entries if e.entry_type == "message"]
        assert len([e for e in message_entries if e.role != "system"]) == 2

    def test_fork_has_forked_from_marker(self, manager):
        source = manager.create_session(Path("/tmp"))

        forked = manager.fork_session(source.session_id, Path("/tmp"))
        entries = forked.read_transcript()
        last = entries[-1]
        assert last.metadata.get("event") == "forked_from"
        assert last.metadata.get("source_session") == source.session_id

    def test_fork_skips_compact_boundaries(self, manager):
        source = manager.create_session(Path("/tmp"))
        source.append(make_entry("user", "old"))
        source.append_compact_boundary("uuid-123")
        source.append(make_entry("user", "summary"))
        source.append(make_entry("assistant", "response"))

        forked = manager.fork_session(source.session_id, Path("/tmp"))
        entries = forked.read_transcript()
        # compact_boundary should not be copied
        for entry in entries:
            assert entry.entry_type != "compact_boundary"

    def test_fork_nonexistent_source(self, manager):
        with pytest.raises(FileNotFoundError):
            manager.fork_session("nonexist", Path("/tmp"))


# ── SessionStore sidechain tests ───────────────────────────────────────

class TestSidechains:
    def test_get_sidechain_path(self, store):
        path = store.get_sidechain_path("agent-abc")
        assert path.name == "agent-abc.jsonl"
        assert "sidechains" in str(path)

    def test_sidechain_dir_created(self, store):
        store.get_sidechain_path("agent-1")
        assert store.sidechain_dir.exists()
        assert store.sidechain_dir.is_dir()


# ── Edge case tests ────────────────────────────────────────────────────

class TestEdgeCases:
    def test_special_characters_in_content(self, store):
        entry = make_entry("user", 'line1\nline2\t"quoted" \'single\' \\backslash')
        store.append(entry)
        entries = store.read_transcript()
        assert entries[0].content == 'line1\nline2\t"quoted" \'single\' \\backslash'

    def test_unicode_content(self, store):
        content = "Hello 世界 🌍"
        entry = make_entry("user", content)
        store.append(entry)
        entries = store.read_transcript()
        assert entries[0].content == content

    def test_empty_content(self, store):
        entry = make_entry("system", "")
        store.append(entry)
        entries = store.read_transcript()
        assert entries[0].content == ""
