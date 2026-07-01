"""Global prompt history. Paper Section 9.1.

User prompts stored in history.jsonl at the Claude configuration home
directory (~/.d2c/history.jsonl). Enables Up-arrow / ctrl+r navigation
in interactive mode.
"""

from __future__ import annotations

import json
import time
from pathlib import Path


class PromptHistory:
    """Global prompt history, stored as JSONL in ~/.d2c/history.jsonl.

    Paper: "history.ts -> makeHistoryReader() yields entries in reverse order."
    """

    def __init__(self, base_dir: Path | None = None):
        if base_dir is None:
            base_dir = Path.home() / ".d2c"
        self._base_dir = base_dir
        self._history_path = base_dir / "history.jsonl"

    @property
    def path(self) -> Path:
        return self._history_path

    def append(self, prompt: str, metadata: dict | None = None) -> dict:
        """Append a prompt to the global history.

        Returns the entry that was written.
        """
        entry = {
            "prompt": prompt,
            "timestamp": time.time(),
            "cwd": str(Path.cwd()),
            "metadata": metadata or {},
        }
        self._base_dir.mkdir(parents=True, exist_ok=True)
        with open(self._history_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def read_reverse(self, limit: int = 1000) -> list[dict]:
        """Read most recent entries in reverse chronological order.

        Paper: "yields entries in reverse order" for Up-arrow navigation.
        """
        if not self._history_path.exists():
            return []

        entries: list[dict] = []
        # Read from end: iterate lines in reverse
        try:
            text = self._history_path.read_text(encoding="utf-8")
            lines = text.strip().split("\n")
            # Take last N lines, reverse them
            for line in reversed(lines[-limit:]):
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            return []

        return entries

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """Fuzzy search history for ctrl+r navigation.

        Matches prompts containing the query substring (case-insensitive).
        Returns most recent matches first.
        """
        if not self._history_path.exists():
            return []

        query_lower = query.lower()
        results: list[dict] = []

        try:
            text = self._history_path.read_text(encoding="utf-8")
            lines = text.strip().split("\n")
            for line in reversed(lines):
                if len(results) >= limit:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if query_lower in entry.get("prompt", "").lower():
                    results.append(entry)
        except OSError:
            return []

        return results

    def clear(self) -> None:
        """Delete the history file."""
        if self._history_path.exists():
            self._history_path.unlink()

    def count(self) -> int:
        """Return the number of entries in the history."""
        if not self._history_path.exists():
            return 0
        try:
            text = self._history_path.read_text(encoding="utf-8")
            return len([ln for ln in text.strip().split("\n") if ln.strip()])
        except OSError:
            return 0
