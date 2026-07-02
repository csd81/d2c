# Phase 76: Textual session ergonomics

**Priority:** HIGH (make the opt-in Textual UI usable for real sessions)

## Context

Phases 74 and 75 proved the Textual path can work:

- opt-in Textual app shell
- transcript/status/input layout
- shared slash-command registry
- Markdown rendering
- approval modal
- compact tool progress rows

The next step is not more feature breadth. It is session ergonomics: scrolling,
input history, keyboard behavior, visual separation, and resize handling. These
determine whether `D2C_TUI=textual` is comfortable enough for real use.

## Goal

Make the Textual UI practical for longer interactive sessions while preserving
the default prompt-toolkit REPL.

Primary goals:

1. Transcript scrollback behaves predictably.
2. Prompt input history works.
3. Common keyboard shortcuts feel natural.
4. User/assistant/tool/error blocks are visually distinct.
5. Small terminal sizes remain usable.

## Scope

In scope:

- transcript auto-scroll behavior
- preserve scroll position when user scrolls up
- input history navigation
- keyboard shortcuts
- block styling and separation
- resize/small-terminal layout handling
- Textual pilot tests for key interactions

Out of scope:

- making Textual the default UI
- changing agent-loop behavior
- changing approval semantics
- adding new slash commands
- mouse-first workflows
- persistent UI layout config
- full release gate

## UX Requirements

### Scrollback

Expected behavior:

- New messages auto-scroll when the user is already at the bottom.
- If the user has scrolled up, new messages should not yank the viewport down.
- Provide quick navigation:
  - `PageUp`
  - `PageDown`
  - `Home`
  - `End`

### Input History

Expected behavior:

- Up/Down navigate prompt history.
- Empty input + Up recalls previous prompts.
- Non-empty input should not be overwritten accidentally without a clear rule.
- Multiline input, if currently supported, should not regress.

### Keyboard Shortcuts

Suggested bindings:

| Key | Behavior |
|---|---|
| `Ctrl+C` | cancel current input, or request safe interruption if a run is active |
| `Ctrl+L` | clear transcript view only; do not clear session/history |
| `Esc` | close modal / cancel current transient UI |
| `PageUp/PageDown` | scroll transcript |
| `Home/End` | transcript top/bottom when focus is transcript |

Be conservative with cancellation. Do not kill tools or mutate session state
unless an existing safe cancellation path already exists.

### Visual Separation

Transcript blocks should make roles obvious:

- user prompt
- assistant Markdown response
- tool row
- denied/error row
- system/status note

Keep styling restrained and terminal-friendly. Avoid large decorative panels
that waste vertical space.

### Resize / Small Terminals

Expected behavior:

- status/footer remains visible or degrades cleanly
- input remains usable
- transcript takes remaining space
- long paths/tool previews truncate gracefully
- no obvious overlap between transcript, status, and input

## Design Direction

Likely files:

```text
src/d2c/tui/app.py
src/d2c/tui/widgets.py
src/d2c/tui/commands.py
tests/test_tui_textual.py
```

Optional:

```text
README.md
CHANGELOG.md
tests/test_phase76_textual_ergonomics.py
```

Prefer improving existing Textual widgets over adding many new abstractions.
If history and scroll state need small helpers, keep them isolated and unit
tested.

## Tests

Use Textual's pilot where possible.

Add tests for:

1. Transcript auto-scrolls when at bottom.
2. Transcript does not auto-scroll when user has scrolled up.
3. `End` returns to latest output.
4. Up/Down navigate input history.
5. `Ctrl+L` clears the visual transcript without clearing session state.
6. `Esc` closes approval modal.
7. Tool error/denied rows have distinct render metadata/classes.
8. Small terminal size does not crash layout construction.

Avoid screenshot tests unless there is no practical alternative. Prefer widget
state, render classes, and captured text.

## Verification

Fast only:

```bash
./scripts/check_fast.sh
python -m pytest tests/test_tui_textual.py
```

Manual Textual smoke:

```bash
D2C_TUI=textual python -m d2c
```

Manual checks:

- send several prompts
- scroll up while output arrives
- use PageUp/PageDown/Home/End
- use Up/Down history
- trigger an approval modal and close it with Esc
- resize terminal smaller and larger

Do not run the full release gate unless explicitly requested.

## Acceptance Criteria

- Textual transcript scrollback is predictable.
- Input history works for normal single-line prompts.
- Common keybindings work and do not mutate session unexpectedly.
- Transcript roles are visually distinct.
- Small terminal layout remains usable.
- Textual remains opt-in via `D2C_TUI=textual`.
- Default prompt-toolkit REPL remains unchanged.
- Fast checks pass.

## Expected Outcome

The Textual UI becomes usable for real sessions rather than just technically
functional. After this phase, the project can make an informed decision about
whether Textual is ready for broader dogfooding or eventual default status.
