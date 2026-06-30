# Phase 23: File History Checkpoints (--rewind-files)

**Paper Reference:** Section 9.2 — "File-history checkpoints for --rewind-files,
stored at ~/.claude/file-history/<sessionId>/."

**Priority:** LOW

## Rationale

The paper specifically mentions file-history checkpoints as a recovery mechanism.
They enable reverting filesystem changes made during a session. This is the paper's
"checkpoints" feature — separate from session transcripts.

## Files to Create/Modify

1. CREATE `src/d2c/file_history.py` — file snapshot and restore
2. MODIFY `src/d2c/main.py` — add `--rewind-files` flag

## Key Design

```python
class FileHistory:
    """File-level snapshots for reverting filesystem changes."""

    def __init__(self, base_dir: Path, session_id: str):
        self.checkpoint_dir = base_dir / "file-history" / session_id

    def checkpoint(self, file_path: Path) -> None:
        """Save a copy of a file before modification."""
        rel_path = file_path.resolve().relative_to(Path.cwd())
        target = self.checkpoint_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if file_path.exists():
            shutil.copy2(file_path, target)

    def rewind(self, file_path: Path) -> bool:
        """Restore a file to its checkpointed state."""
        ...

    def rewind_all(self) -> list[Path]:
        """Restore all checkpointed files."""
        ...

class FileHistoryTracker:
    """Wraps WriteTool and EditTool to automatically checkpoint before writes."""
    def before_write(self, file_path: Path) -> None:
        file_history.checkpoint(file_path)
```

## Integration

```python
# Before writing through any tool:
file_history.before_write(file_path)
```

## CLI

```bash
d2c --rewind-files  # reverts all file changes from this session
```

## Edge Cases

- File not checkpointed (never written) → rewind is no-op
- Binary files → checkpoints work as binary copies
- Checkpoint directory grows large → per-session, cleaned on session end
- File deleted after checkpoint → rewind restores it

## Tests (~6)

- Checkpoint saves file copy
- Rewind restores file from checkpoint
- Rewind all restores multiple files
- File not checkpointed → rewind no-op
- Checkpoint with nested directories
- Empty checkpoint dir → rewind all no-op
