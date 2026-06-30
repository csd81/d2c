"""Tests for Phase 23: File History Checkpoints."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from d2c.file_history import FileHistory, FileHistoryTracker


# ── FileHistory tests ───────────────────────────────────────────────────

class TestFileHistory:
    def test_checkpoint_saves_file_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_dir = root / ".d2c"
            cwd = root / "project"
            cwd.mkdir()
            src_file = cwd / "hello.py"
            src_file.write_text("original content")

            fh = FileHistory(base_dir, "session-1", cwd=cwd)
            assert fh.checkpoint(src_file)
            assert src_file in fh.checkpointed_files

            # Modify source
            src_file.write_text("modified content")

            # Rewind
            assert fh.rewind(src_file)
            assert src_file.read_text() == "original content"

    def test_rewind_all_restores_multiple_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_dir = root / ".d2c"
            cwd = root / "project"
            cwd.mkdir()

            file_a = cwd / "a.py"
            file_b = cwd / "b.py"
            file_a.write_text("a original")
            file_b.write_text("b original")

            fh = FileHistory(base_dir, "session-2", cwd=cwd)
            fh.checkpoint(file_a)
            fh.checkpoint(file_b)

            file_a.write_text("a modified")
            file_b.write_text("b modified")

            restored = fh.rewind_all()
            assert len(restored) == 2
            assert file_a.read_text() == "a original"
            assert file_b.read_text() == "b original"

    def test_no_checkpoint_rewind_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_dir = root / ".d2c"
            cwd = root / "project"
            cwd.mkdir()
            src_file = cwd / "never_checkpointed.py"
            src_file.write_text("original")

            fh = FileHistory(base_dir, "session-3", cwd=cwd)
            result = fh.rewind(src_file)
            assert result is False
            assert src_file.read_text() == "original"  # unchanged

    def test_checkpoint_non_existent_file(self):
        """Checkpointing a file that doesn't exist tracks it for future rewind."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_dir = root / ".d2c"
            cwd = root / "project"
            cwd.mkdir()
            new_file = cwd / "will_be_created.py"

            fh = FileHistory(base_dir, "session-4", cwd=cwd)
            result = fh.checkpoint(new_file)
            assert result is True  # tracked even though no checkpoint file created
            assert new_file in fh.checkpointed_files

    def test_checkpoint_only_once(self):
        """Second checkpoint of same file is a no-op."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_dir = root / ".d2c"
            cwd = root / "project"
            cwd.mkdir()
            src_file = cwd / "data.txt"
            src_file.write_text("version 1")

            fh = FileHistory(base_dir, "session-5", cwd=cwd)
            assert fh.checkpoint(src_file)
            assert not fh.checkpoint(src_file)  # Already checkpointed

    def test_checkpoint_nested_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_dir = root / ".d2c"
            cwd = root / "project"
            cwd.mkdir()
            nested = cwd / "a" / "b" / "c"
            nested.mkdir(parents=True)
            deep_file = nested / "deep.py"
            deep_file.write_text("deep content")

            fh = FileHistory(base_dir, "session-6", cwd=cwd)
            fh.checkpoint(deep_file)

            deep_file.write_text("modified")
            fh.rewind(deep_file)
            assert deep_file.read_text() == "deep content"

    def test_rewind_restores_deleted_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_dir = root / ".d2c"
            cwd = root / "project"
            cwd.mkdir()
            src_file = cwd / "restore_me.py"
            src_file.write_text("save me")

            fh = FileHistory(base_dir, "session-7", cwd=cwd)
            fh.checkpoint(src_file)

            src_file.unlink()
            assert not src_file.exists()

            assert fh.rewind(src_file)
            assert src_file.exists()
            assert src_file.read_text() == "save me"

    def test_rewind_all_empty_no_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_dir = root / ".d2c"
            cwd = root / "project"
            cwd.mkdir()

            fh = FileHistory(base_dir, "session-8", cwd=cwd)
            restored = fh.rewind_all()
            assert restored == []

    def test_cleanup_removes_checkpoint_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_dir = root / ".d2c"
            cwd = root / "project"
            cwd.mkdir()
            src_file = cwd / "temp.txt"
            src_file.write_text("temp")

            fh = FileHistory(base_dir, "session-9", cwd=cwd)
            fh.checkpoint(src_file)
            assert fh.checkpoint_dir.exists()

            fh.cleanup()
            assert not fh.checkpoint_dir.exists()

    def test_list_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_dir = root / ".d2c"

            fh1 = FileHistory(base_dir, "abc", cwd=root)
            fh1.checkpoint_dir.mkdir(parents=True, exist_ok=True)
            fh2 = FileHistory(base_dir, "xyz", cwd=root)
            fh2.checkpoint_dir.mkdir(parents=True, exist_ok=True)

            sessions = FileHistory.list_sessions(base_dir)
            assert "abc" in sessions
            assert "xyz" in sessions

    def test_rewind_session_class_method(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_dir = root / ".d2c"
            cwd = root / "project"
            cwd.mkdir()

            # Setup: checkpoint a file manually
            fh = FileHistory(base_dir, "session-rw", cwd=cwd)
            src_file = cwd / "data.txt"
            src_file.write_text("original")
            fh.checkpoint(src_file)

            # Modify
            src_file.write_text("changed")

            # Rewind via class method
            restored = FileHistory.rewind_session(base_dir, "session-rw", cwd=cwd)
            assert len(restored) >= 1
            assert src_file.read_text() == "original"


# ── FileHistoryTracker tests ────────────────────────────────────────────

class TestFileHistoryTracker:
    def test_before_write_checkpoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_dir = root / ".d2c"
            cwd = root / "project"
            cwd.mkdir()
            src_file = cwd / "tracked.txt"
            src_file.write_text("before")

            fh = FileHistory(base_dir, "s", cwd=cwd)
            tracker = FileHistoryTracker(fh)
            tracker.before_write(src_file)

            src_file.write_text("after")
            fh.rewind(src_file)
            assert src_file.read_text() == "before"
