# Phase 75: Textual approval modal and tool progress timeline

**Priority:** HIGH (next Textual parity slice)

## Context

Phase 74 introduced an opt-in Textual TUI behind `D2C_TUI=textual`, while
leaving the default prompt-toolkit REPL unchanged. It added the foundation:

- Textual app shell
- transcript/status/input layout
- shared command registry reuse
- Rich Markdown rendering
- lazy Textual imports and headless path isolation

The next highest-value parity gap is the approval and tool-progress experience.
Prompt-toolkit already has approval dialogs and compact tool feedback; Textual
needs equivalent behavior before it can become the default UI.

## Goal

Bring two core interactive surfaces to the Textual UI:

1. Permission approval modal with the exact existing scopes.
2. Tool progress/timeline rows for live tool execution visibility.

Keep Textual opt-in until these surfaces are stable.

## Scope

In scope:

- Textual approval modal/dialog
- choice handling for `[y]`, `[a]`, `[A]`, `[n]`
- risk/category/input preview
- redaction through existing observability helpers
- optional diff summary/expansion for file-changing tools
- Textual tool-event timeline rows
- tests for modal mapping and row rendering

Out of scope:

- making Textual the default UI
- changing approval semantics
- changing permission engine behavior
- full-screen diff viewer
- mouse-first workflows
- editing approvals from the modal
- replacing prompt-toolkit implementation

## Approval Modal Requirements

Reuse the existing approval model from Phases 52, 64, and 65.

Choices:

| Key | Meaning |
|---|---|
| `y` | approve once |
| `a` | approve exact action for current session |
| `A` | persist exact action across sessions |
| `n` / Escape / default | deny |

Display:

- tool name
- permission category
- reason/risk
- sanitized input preview
- approval scope choices
- default deny

For Bash:

- show command string directly after redaction
- reuse existing shell risk/category helper if available

For Edit/Write/ApplyPatch:

- show file path(s)
- show compact `+N / -M` diff summary where possible
- support an optional expanded diff area if cheap
- never read extra files speculatively

Security:

- use existing `redact()` behavior
- never show secrets
- never show persistent approval hashes
- never bypass deny rules

## Tool Progress Timeline

Render tool activity as compact rows in the Textual transcript or a dedicated
timeline area.

Suggested row:

```text
Read       src/d2c/main.py                         ok
Bash       pytest tests/test_repl_ux.py            2.4s ok
ApplyPatch src/d2c/main.py                         +42 -8 ok
WebSearch  "textual python tui"                    3 results ok
```

Fields:

- tool name
- compact target/input preview
- status: running / ok / error / denied
- duration if available
- short error summary if failed

Keep full output behavior unchanged. The row is a progress/summary surface, not
a replacement for transcript tool results.

## Design Direction

Likely files:

```text
src/d2c/tui/approvals.py
src/d2c/tui/widgets.py
src/d2c/tui/app.py
src/d2c/main.py
tests/test_tui_textual.py
```

Reuse existing helpers where possible:

- approval choice types from `src/d2c/tui/approvals.py` or existing Phase 74
  boundary
- `_tool_input_preview` / diff summary helpers from `main.py`, or move them to a
  shared helper if needed
- `observability.redact`
- Phase 72 command registry remains unchanged

Avoid duplicating approval semantics in Textual-specific code. Textual should
collect a choice; existing approval cache/application code should decide what
that choice means.

## Tests

Add or update tests for:

1. Modal key choices map to the existing approval scopes.
2. Escape/unknown/default maps to deny.
3. Bash preview is redacted and risk-labeled.
4. Edit/Write/ApplyPatch preview shows compact diff/file summary.
5. Tool progress row renders name, preview, status, and duration.
6. Error/denied rows are visibly distinct in render data.
7. Textual path remains opt-in via `D2C_TUI=textual`.
8. Default prompt-toolkit REPL behavior remains unchanged.

Prefer testing renderable objects or helper output over terminal screenshots.

## Verification

Fast only:

```bash
./scripts/check_fast.sh
python -m pytest tests/test_tui_textual.py tests/test_phase65_ui_dialog.py
```

Manual Textual smoke:

```bash
D2C_TUI=textual python -m d2c
/help
/approvals
```

Then trigger a permission-requiring tool call and verify:

- modal appears
- `[n]` denies by default
- `[y]`, `[a]`, and `[A]` map correctly
- tool rows appear during/after execution

Do not run the full release gate unless explicitly requested.

## Acceptance Criteria

- Textual approval modal exists and preserves existing approval scopes.
- Deny remains the default.
- Redaction and no-speculative-read constraints hold.
- Tool events render as compact progress/timeline rows.
- Textual remains opt-in.
- Prompt-toolkit behavior is unchanged.
- Fast checks pass.

## Expected Outcome

The Textual UI moves from a shell/prototype to a useful interactive mode. It
can handle the two most important live workflow surfaces: permission decisions
and tool execution visibility, while still preserving the mature prompt-toolkit
REPL as the default.
