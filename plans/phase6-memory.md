# Phase 6: Memory System

## Files

- `src/d2c/memory.py` — loadClaudeMdHierarchy(), AutoMemoryStore, LazyMemoryLoader
- `tests/test_memory.py`

## CLAUDE.md 4-Level Hierarchy

1. Managed — /etc/d2c/CLAUDE.md
2. User — ~/.d2c/CLAUDE.md
3. Project — CLAUDE.md, .d2c/CLAUDE.md, .d2c/rules/*.md
4. Local — CLAUDE.local.md (gitignored)

Files load in reverse priority order (later-loaded = more model attention). Directory traversal: root → cwd.

## Lazy Loading

Nested-directory instruction files load lazily when agent reads files in those directories. Directories above cwd load eagerly at startup.

## Auto Memory

- Files: ~/.d2c/memory/*.md with frontmatter (name, description, type)
- Index: MEMORY.md with one-line entries
- Types: user, feedback, project, reference

## @include Directive

`@path`, `@./relative`, `@~/home`, `@/absolute`. Only in leaf text (not code blocks). Circular prevention via tracked paths. Non-existent files silently ignored.
