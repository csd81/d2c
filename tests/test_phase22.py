"""Tests for Phase 22: Global Prompt History."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from d2c.history import PromptHistory


# ── PromptHistory tests ─────────────────────────────────────────────────

class TestPromptHistory:
    def test_append_and_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = PromptHistory(Path(tmp) / ".d2c")
            h.append("Hello world")
            h.append("Another prompt")
            assert h.count() == 2

    def test_read_reverse_most_recent_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = PromptHistory(Path(tmp) / ".d2c")
            h.append("First prompt")
            h.append("Second prompt")
            h.append("Third prompt")

            entries = h.read_reverse(limit=10)
            assert len(entries) == 3
            assert entries[0]["prompt"] == "Third prompt"
            assert entries[1]["prompt"] == "Second prompt"
            assert entries[2]["prompt"] == "First prompt"

    def test_entry_has_required_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = PromptHistory(Path(tmp) / ".d2c")
            entry = h.append("Test prompt", metadata={"mode": "interactive"})

            assert "prompt" in entry
            assert "timestamp" in entry
            assert "cwd" in entry
            assert "metadata" in entry
            assert entry["prompt"] == "Test prompt"
            assert entry["metadata"]["mode"] == "interactive"

    def test_search_finds_matching_prompts(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = PromptHistory(Path(tmp) / ".d2c")
            h.append("Fix the login bug")
            h.append("Add new feature")
            h.append("Refactor auth module")

            results = h.search("auth", limit=10)
            assert len(results) >= 1
            assert any("auth" in r["prompt"].lower() for r in results)

    def test_search_case_insensitive(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = PromptHistory(Path(tmp) / ".d2c")
            h.append("HELLO WORLD")

            results_lower = h.search("hello")
            results_upper = h.search("HELLO")
            assert len(results_lower) == 1
            assert len(results_upper) == 1

    def test_search_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = PromptHistory(Path(tmp) / ".d2c")
            for i in range(10):
                h.append(f"test prompt {i}")

            results = h.search("test", limit=3)
            assert len(results) <= 3
            # Most recent first
            assert "9" in results[0]["prompt"]

    def test_empty_history_no_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = PromptHistory(Path(tmp) / ".d2c")
            assert h.count() == 0
            assert h.read_reverse() == []
            assert h.search("anything") == []
            h.clear()  # should not raise

    def test_clear_removes_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = PromptHistory(Path(tmp) / ".d2c")
            h.append("Some prompt")
            assert h.count() == 1
            h.clear()
            assert h.count() == 0

    def test_read_reverse_respects_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = PromptHistory(Path(tmp) / ".d2c")
            for i in range(5):
                h.append(f"prompt {i}")

            entries = h.read_reverse(limit=2)
            assert len(entries) == 2
            assert entries[0]["prompt"] == "prompt 4"

    def test_corrupt_lines_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / ".d2c"
            base.mkdir(parents=True)
            hist_path = base / "history.jsonl"

            # Write a mix of valid and invalid JSON lines
            hist_path.write_text(
                json.dumps({"prompt": "good", "timestamp": 1, "cwd": "/", "metadata": {}}) + "\n"
                "this is not valid json\n"
                + json.dumps({"prompt": "also good", "timestamp": 2, "cwd": "/", "metadata": {}}) + "\n"
            )

            h = PromptHistory(base)
            # count() counts all non-empty lines (3 lines)
            assert h.count() == 3
            # read_reverse skips corrupt lines (returns 2 valid entries)
            entries = h.read_reverse()
            assert len(entries) == 2

    def test_search_no_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = PromptHistory(Path(tmp) / ".d2c")
            h.append("Some prompt")
            h.append("Another one")

            results = h.search("xyz_nonexistent_xyz")
            assert results == []
