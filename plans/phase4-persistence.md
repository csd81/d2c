# Phase 4: Session Persistence

## Files

- `src/d2c/persistence.py` — SessionEntry, SessionManifest, SessionStore, SessionManager
- `tests/test_persistence.py`

## Key Types

- `SessionEntry`: role, content, timestamp, entry_type ("message"|"compact_boundary"|"subagent_summary"), metadata
- `SessionStore`: append(), append_compact_boundary(), read_transcript(), reconstruct_messages(), get_sidechain_path()
- `SessionManager`: create_session(), resume_session(), fork_session()

## Storage Layout

```
~/.d2c/sessions/
  {session_id}.jsonl          # append-only transcript
  {session_id}.manifest.json  # lightweight index
  {session_id}_sidechains/    # subagent transcripts
    {subagent_id}.jsonl
```

## Key Design Decision

Resume and fork rebuild messages from JSONL but do NOT restore session-scoped permissions.
Trust is re-established per session.

## Edge Cases

- Resume nonexistent session → error
- Concurrent writes → file-level locking
- Corrupt JSON line → skip, log warning
- compact_boundary entries → reconstruct_messages() handles boundary logic
