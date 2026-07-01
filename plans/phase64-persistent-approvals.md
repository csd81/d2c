 # Phase 64: Persistent cross-session approval cache

 **Goal:** Approvals saved with `a` ("always") survive process restarts and are
 shared across sessions. Security is preserved: only SHA-256 hashes on disk (no
 plaintext tool input), and the file respects the same trust boundaries.

 ## Files to change

 ### 1. `src/d2c/approvals.py`

 Add an `ApprovalCache.load()` / `.save()` pair reading/writing
 `~/.d2c/approvals.json` (a dict of `{sha256_hash: iso_timestamp}`). The file is
 atomic (write to a `.tmp` then rename). On `clear()`, the file is deleted or
 emptied. Timestamps let you optionally expire old entries (e.g., 30 days)
 during `load()` — not essential for v1, but the schema should allow it.

 Constructor gets an optional `path: Path | None` — defaults to
 `~/.d2c/approvals.json`. Passing `path=None` keeps the current in-memory-only
 behavior (for tests that don't want disk I/O).

 ### 2. `src/d2c/main.py`

 - `_new_approval_cache()` passes the persistent path to `ApprovalCache()`,
   which loads existing hashes from disk.
 - Session-switch semantics: `clear()` empties the runtime set only (no disk
   write). Add a `reset()` method that nukes both. Session switch calls
   `clear()` (runtime only). If the user wants to wipe all approvals, they can
   delete the file manually.

 ### 3. `tests/test_phase64_approvals.py`

 New test file covering:
 - `ApprovalCache(path=tmp)` persists and reloads approvals across process
   restarts
 - Integrity: corrupted JSON doesn't crash — falls back to empty cache, logs a
   warning
 - Timestamps recorded correctly
 - `clear()` doesn't touch the disk file
 - `reset()` removes the disk file and clears runtime keys
 - Concurrent safety (a lock around disk writes matching the existing prompt
   lock pattern)
 - Cross-session approval still works end-to-end via the REPL callback

 ### 4. `COMPARISON.md` and `CHANGELOG.md`

 Brief note that Phase 64 added persistent cross-session approvals.

 ## What's intentionally *not* changing

 - The hash scheme stays SHA-256 over `{tool, category, input}` — same
   conservative exact-match design.
 - No migration or expiry logic in v1 (the ISO timestamp in the JSON is
   forward-compatible).
 - No admin CLI or `/approvals` command — the file is user-serviceable by
   deleting it.
 - The `[y/N/a]` prompt UX is unchanged.
 - The existing test for hash-only storage gets updated: the check for
   `not hasattr(c, "save")` is replaced by asserting that hashes (not
   plaintext) are on disk.

 ## Risk

 Low. The write path is on a user-triggered action ("a" keypress, not on every
 tool call). The read path is once at startup. Atomic write via `.tmp` + rename
 prevents corruption. The fallback on bad JSON is to log a warning and start
 empty.

 ## Security notes

 - Only SHA-256 hashes touch disk, never tool input or command text.
 - The file lives in `~/.d2c/`, which already stores trusted data
   (`trusted.json`, sessions). No new permission boundary is needed.
 - Timestamp entries do not leak command content.
 - A process reading the file cannot reconstruct the original commands.
