# Phase 74: Textual TUI rework

**Priority:** HIGH-RISK / HIGH-UPSIDE (major UI architecture change)

## Context

Claude Code's TypeScript implementation uses Ink for a rich terminal UI. The
closest Python equivalent is Textual: a component-based TUI framework with
widgets, layout, async support, keyboard handling, and Rich-powered rendering.

`d2c` currently uses `prompt_toolkit` for the interactive REPL, status bar,
autocomplete, permission dialogs, and styled command output. This works, but the
UI is becoming increasingly complex: Markdown rendering, slash-command
discovery, approval dialogs, profile inspection, usage/status surfaces, and live
tool progress all want a more structured terminal app.

Phase 74 should plan a Textual migration carefully. A direct one-shot rewrite of
all interactive surfaces is risky; the safer approach is to introduce a Textual
app shell and migrate UI surfaces behind existing behavioral contracts.

## Goal

Rework the interactive TUI to use Textual as the primary UI framework while
preserving existing non-interactive behavior.

Primary goals:

1. Replace prompt-toolkit-driven interactive UI with a Textual app shell.
2. Render assistant Markdown using Rich/Textual.
3. Preserve slash commands, autocomplete-like command entry, approvals,
   profiles, usage/status, and live tool progress.
4. Keep headless, SDK, MCP, eval, and server surfaces unchanged.
5. Preserve existing safety semantics exactly.

## Scope

In scope:

- Textual dependency and app shell
- conversation transcript view
- input box / command entry
- slash-command handling integration
- Markdown rendering for assistant responses
- permission approval modal/dialog
- status/footer bar
- tool progress/event display
- tests for routing, rendering helpers, and non-interactive boundaries

Out of scope:

- changing agent loop behavior
- changing permission semantics
- changing session persistence format
- web UI
- mouse-first UX
- remote collaboration
- replacing Rich/Textual internals
- removing prompt_toolkit immediately if still needed as a fallback

## Migration Strategy

Do not rewrite everything in one opaque diff. Use a staged migration.

### Stage 1: UI Boundary Extraction

Extract interactive UI responsibilities from `main.py` into a small interface:

```python
class InteractiveUI(Protocol):
    async def read_prompt(self) -> str: ...
    def render_user_message(self, text: str) -> None: ...
    def render_assistant_message(self, text: str) -> None: ...
    def render_tool_event(self, event: ToolExecutionEvent) -> None: ...
    async def request_approval(self, request, result) -> ApprovalChoice: ...
    def render_status(self, state) -> None: ...
```

Keep the existing prompt-toolkit implementation as the first adapter if useful.

### Stage 2: Textual App Shell

Add a new Textual-backed UI implementation:

```text
src/d2c/tui/
├── __init__.py
├── app.py
├── widgets.py
├── markdown.py
├── commands.py
└── approvals.py
```

Suggested layout:

```text
┌──────────────── transcript ────────────────┐
│ user / assistant / tool events             │
│ markdown-rendered assistant responses      │
│ compact tool progress                      │
├──────────────── status bar ────────────────┤
│ model | mode | trust | cwd | usage | tasks │
├──────────────── input ─────────────────────┤
│ > prompt or /command                       │
└────────────────────────────────────────────┘
```

### Stage 3: Slash Commands

Reuse the Phase 72 command registry. Textual should not invent a second command
system.

Support:

- command entry
- command suggestions/completion
- grouped help rendering
- unknown-command suggestions
- `/approvals`
- `/profiles`
- `/usage`
- `/settings`
- `/clear`, `/resume`, `/fork`, `/exit`

### Stage 4: Permission Dialog

Port the Phase 65 approval dialog into a Textual modal.

Requirements:

- `[y]` once
- `[a]` session
- `[A]` persistent
- `[n]` deny default
- optional diff expansion
- secret redaction
- category/risk styling
- no speculative file reads

### Stage 5: Markdown Rendering

Use Rich's Markdown rendering inside Textual for completed assistant messages.

Rules:

- render completed assistant messages, not partial deltas, in v1
- preserve code fences
- keep links visible
- do not execute/fetch/resolve anything
- fallback to plain text on render errors

### Stage 6: Tool Progress

Render tool events as compact timeline rows:

```text
Read       src/d2c/main.py                         ok
Bash       pytest tests/test_repl_ux.py            2.4s ok
ApplyPatch src/d2c/main.py                         +42 -8
```

Keep full tool output available only when it is already part of the transcript
or via an explicit expansion command/widget.

## Dependency

Add:

```toml
textual>=0.80
```

Textual pulls Rich. Pin conservatively enough to avoid churn but not so tightly
that installation becomes fragile.

## Files to Inspect / Modify

Likely:

```text
pyproject.toml
src/d2c/main.py
src/d2c/tui/
tests/test_repl_ux.py
tests/test_repl_commands.py
tests/test_phase65_ui_dialog.py
tests/test_phase72_repl_command_ux.py
```

Optional:

```text
README.md
CHANGELOG.md
docs/security.md
plans/phase74-textual-tui-rework.md
tests/test_tui_textual.py
```

## Compatibility

Preserve:

- `python -m d2c "prompt"` headless behavior
- `--json`
- `--mcp`
- `--serve`
- SDK behavior
- eval harness behavior
- approval cache semantics
- transcript persistence

Consider a temporary fallback flag if needed:

```text
D2C_TUI=prompt_toolkit
D2C_TUI=textual
```

Default can remain prompt-toolkit until Textual parity is verified, then switch
default in a later commit if the migration is large.

## Tests

Add tests for:

1. Textual app can instantiate without starting a real terminal.
2. Slash-command registry is reused by Textual command entry.
3. Markdown renderer preserves visible text/code.
4. Permission modal choices map to existing approval scopes.
5. Status bar renders model/mode/trust/cwd/usage fields.
6. Headless path does not import/start Textual app.
7. Existing prompt-toolkit tests either still pass or are intentionally replaced.

Prefer unit tests around adapters/widgets over brittle terminal screenshots.

## Verification

Fast only:

```bash
./scripts/check_fast.sh
python -m pytest tests/test_repl_ux.py tests/test_repl_commands.py tests/test_tui_textual.py
```

Manual smoke:

```bash
python -m d2c
/help
/usage
/approvals
/profiles
```

Do not run the full release gate unless explicitly requested.

## Risks

| Risk | Mitigation |
|---|---|
| Large rewrite destabilizes REPL | Stage behind `InteractiveUI` boundary |
| Textual event loop conflicts with agent loop | Prototype async integration first |
| Tests become brittle | Test adapters and render data, not terminal pixels |
| Dependency churn | Conservative version bound and small usage surface |
| Permission UX regression | Reuse existing approval decision code and tests |
| Headless regression | Keep Textual isolated from non-interactive paths |

## Acceptance Criteria

- Textual app shell exists and can run the interactive REPL.
- Core slash commands work through the Textual UI.
- Assistant Markdown renders through Rich/Textual.
- Permission approvals preserve existing scopes and safety semantics.
- Status/footer displays model, permission mode, trust, cwd, usage, and
  background task count where available.
- Headless, SDK, MCP, eval, and server paths are unchanged.
- Fast checks pass.

## Expected Outcome

The interactive experience moves from an organically grown prompt-toolkit REPL
to a structured Textual terminal app. This creates a better foundation for
Markdown rendering, richer dialogs, command discovery, live tool progress, and
future interactive workflows without disturbing the core agent architecture.
