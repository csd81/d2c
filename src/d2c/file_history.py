"""File history checkpoints. Paper Section 9.2.

File-level snapshots for reverting filesystem changes made during a
session. Checkpoints stored at ~/.d2c/file-history/<sessionId>/.
The --rewind-files CLI flag restores all checkpointed files.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


class FileHistory:
    """File-level snapshots for reverting filesystem changes.

    Paper: "File-history checkpoints for --rewind-files, stored at
    ~/.claude/file-history/<sessionId>/."

    Before any Write/Edit tool modifies a file, a snapshot is saved.
    The --rewind-files flag restores all files to their checkpointed state.
    """

    def __init__(self, base_dir: Path, session_id: str, cwd: Path | None = None):
        self._base_dir = base_dir
        self._session_id = session_id
        self._cwd = (cwd or Path.cwd()).resolve()
        self._checkpoint_dir = base_dir / "file-history" / session_id
        self._checkpointed: set[Path] = set()

    @property
    def checkpoint_dir(self) -> Path:
        return self._checkpoint_dir

    @property
    def checkpointed_files(self) -> set[Path]:
        return set(self._checkpointed)

    def checkpoint(self, file_path: Path) -> bool:
        """Save a copy of a file before modification.

        Only checkpoints a file once (first write). Returns True if a
        new checkpoint was created, False if already checkpointed.
        """
        resolved = file_path.resolve()

        if resolved in self._checkpointed:
            return False

        if not resolved.exists():
            # File doesn't exist yet — nothing to checkpoint
            # (it will be created by the tool, and we'll track it)
            self._checkpointed.add(resolved)
            return True

        # Compute relative path from cwd for checkpoint storage
        try:
            rel_path = resolved.relative_to(self._cwd)
        except ValueError:
            # File outside cwd — use absolute path as key
            rel_path = Path(str(resolved).replace(":", "").replace("\\", "/").lstrip("/"))

        target = self._checkpoint_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)

        try:
            shutil.copy2(resolved, target)
            self._checkpointed.add(resolved)
            logger.debug("Checkpointed: %s", resolved)
            return True
        except OSError as e:
            logger.warning("Failed to checkpoint %s: %s", resolved, e)
            return False

    def rewind(self, file_path: Path) -> bool:
        """Restore a single file to its checkpointed state.

        Returns True if the file was restored, False if no checkpoint exists.
        """
        resolved = file_path.resolve()

        try:
            rel_path = resolved.relative_to(self._cwd)
        except ValueError:
            rel_path = Path(str(resolved).replace(":", "").replace("\\", "/").lstrip("/"))

        checkpoint_path = self._checkpoint_dir / rel_path

        if not checkpoint_path.exists():
            return False

        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(checkpoint_path, resolved)
            logger.info("Rewound: %s", resolved)
            return True
        except OSError as e:
            logger.warning("Failed to rewind %s: %s", resolved, e)
            return False

    def rewind_all(self) -> list[Path]:
        """Restore all checkpointed files. Returns list of restored paths."""
        restored: list[Path] = []

        if not self._checkpoint_dir.exists():
            return restored

        for resolved in sorted(self._checkpointed):
            try:
                rel_path = resolved.relative_to(self._cwd)
            except ValueError:
                rel_path = Path(str(resolved).replace(":", "").replace("\\", "/").lstrip("/"))

            checkpoint_path = self._checkpoint_dir / rel_path
            if checkpoint_path.exists():
                try:
                    resolved.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(checkpoint_path, resolved)
                    restored.append(resolved)
                except OSError as e:
                    logger.warning("Failed to rewind %s: %s", resolved, e)

        return restored

    def cleanup(self) -> None:
        """Remove all checkpoint data for this session."""
        if self._checkpoint_dir.exists():
            shutil.rmtree(self._checkpoint_dir, ignore_errors=True)

    @classmethod
    def list_sessions(cls, base_dir: Path) -> list[str]:
        """List all session IDs that have file history checkpoints."""
        history_dir = base_dir / "file-history"
        if not history_dir.exists():
            return []
        return sorted(d.name for d in history_dir.iterdir() if d.is_dir())

    @classmethod
    def rewind_session(cls, base_dir: Path, session_id: str, cwd: Path | None = None) -> list[Path]:
        """Rewind all files for a given session ID. Convenience class method."""
        fh = cls(base_dir, session_id, cwd=cwd)
        # Restore all checkpointed files by scanning the checkpoint directory
        restored: list[Path] = []
        checkpoint_dir = fh.checkpoint_dir

        if not checkpoint_dir.exists():
            return restored

        cwd_path = (cwd or Path.cwd()).resolve()

        for cp_file in sorted(checkpoint_dir.rglob("*")):
            if not cp_file.is_file():
                continue
            rel = cp_file.relative_to(checkpoint_dir)
            target = cwd_path / rel
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(cp_file, target)
                restored.append(target)
            except OSError as e:
                logger.warning("Failed to rewind %s: %s", target, e)

        return restored


class FileHistoryTracker:
    """Wraps tool execution to automatically checkpoint files before writes.

    Paper: "FileHistoryTracker wraps WriteTool and EditTool to
    automatically checkpoint before writes."
    """

    def __init__(self, file_history: FileHistory):
        self._fh = file_history

    def before_write(self, file_path: Path) -> None:
        """Call before any Write/Edit tool modifies a file."""
        self._fh.checkpoint(file_path)

    @property
    def file_history(self) -> FileHistory:
        return self._fh
