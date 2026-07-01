# Phase 57: TUI, statusline, and permission dialog polish

**Priority:** MEDIUM-HIGH (User-facing polish and decision quality)

## Context

`d2c` has a functional prompt-toolkit REPL and real slash commands, but Claude Code has a richer
interactive surface: statusline, progress display, permission dialogs, and clearer session state.

## Goal

Improve the interactive UI without rewriting the whole app:

1. richer statusline
2. clearer permission prompt/dialog
3. live tool progress summaries
4. background task/status visibility
5. readable usage/session state

## Scope

In scope:

- prompt-toolkit UI polish
- statusline fields
- permission prompt formatting
- `/status` or improved `/settings`
- tests for formatting

Out of scope:

- full Ink/React clone
- curses/full-screen UI
- web UI
- remote collaboration UI

## Design

Statusline should show compactly:

- session id short form
- model
- permission mode
- cwd basename
- trust status
- active background tasks
- estimated usage/cost if Phase 55 landed

Permission prompt should show:

- tool name
- risk category
- reason
- sanitized input preview
- choices `[y/N/a]`

## Tests

Add tests for:

- statusline rendering with long cwd/model names
- no secret leakage
- permission prompt default deny
- approval choices still work
- terminal-width truncation

## Verification

Run:

```bash
pytest tests/test_repl_ux.py tests/test_repl_commands.py
pytest
ruff check .
ruff format --check .
mypy src/d2c
bandit -c pyproject.toml -r src/d2c
pip-audit
python -m build
twine check dist/*
```

## Acceptance Criteria

- REPL presents session/model/mode/trust state clearly.
- Permission prompts are easier to inspect.
- Formatting handles narrow terminals.
- Full gate suite remains green.

