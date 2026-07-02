# Phase 77: Textual default-readiness audit

**Priority:** HIGH (decide whether Textual can become the default UI)

## Context

Phases 74-76 built the opt-in Textual UI:

- Textual app shell
- transcript/status/input layout
- shared slash-command registry
- Rich Markdown rendering
- approval modal
- tool progress timeline
- scrollback, input history, keybindings, visual separation, and resize handling

The default interactive UI is still prompt-toolkit. Before changing that, the
project needs a disciplined parity and stability audit. The goal is to decide
whether Textual is ready to become the default, not to assume it is.

## Goal

Evaluate Textual against the current prompt-toolkit REPL and produce a clear
go/no-go decision.

Primary questions:

1. Does Textual cover the workflows users rely on today?
2. Are there any safety or approval regressions?
3. Are headless, SDK, MCP, eval, and server paths still isolated from Textual?
4. Is there a clean fallback path if Textual becomes default?
5. Should the project add an explicit `--tui` selector before flipping defaults?

## Scope

In scope:

- prompt-toolkit vs Textual parity checklist
- focused manual smoke matrix
- automated tests for any discovered gaps
- optional `--tui classic|textual|auto` CLI flag
- documentation of known gaps and default decision

Out of scope:

- adding major new Textual features
- command palette
- mouse-first workflows
- removing prompt-toolkit fallback
- default flip unless criteria are clearly met
- full release gate

## Audit Matrix

Compare both UIs for:

| Area | Checks |
|---|---|
| Startup | `python -m d2c`, trusted/untrusted workspace, missing API key |
| Basic loop | prompt entry, assistant response, Markdown display |
| Slash commands | `/help`, `/settings`, `/usage`, `/approvals`, `/profiles`, `/clear`, `/resume`, `/fork`, `/exit` |
| Unknown commands | typo suggestion and `/help` fallback |
| Tool execution | read tool, write/edit preview, error row, denied row |
| Approvals | deny, approve once, session approval, persistent approval |
| Status | model, permission mode, trust, cwd, usage, background tasks |
| Keyboard | Up/Down history, PageUp/PageDown, Home/End, Ctrl+C, Ctrl+L, Esc |
| Resize | small terminal, wide terminal, long paths, long commands |
| Non-interactive | headless prompt, `--json`, SDK, MCP, eval, server unchanged |

## CLI Selector

Consider adding:

```text
--tui auto|classic|textual
```

Suggested behavior:

| Value | Behavior |
|---|---|
| `classic` | force prompt-toolkit |
| `textual` | force Textual; error or fallback clearly if unavailable |
| `auto` | current default policy |

Environment variable remains useful:

```text
D2C_TUI=textual
D2C_TUI=classic
```

Precedence:

1. CLI `--tui`
2. `D2C_TUI`
3. project default

Do not flip the project default in the same change unless the audit passes
cleanly and the fallback is documented.

## Go / No-Go Criteria

Textual can become default only if:

- all critical prompt-toolkit workflows have Textual parity
- approval scopes and deny-default behavior are unchanged
- headless/SDK/MCP/eval/server paths do not import or start Textual
- fallback to classic UI is documented and tested
- fast checks pass
- no known severe resize/input/history bugs remain

If any criterion fails, keep Textual opt-in and document the blocking gaps.

## Deliverable

Add an audit report:

```text
docs/textual-readiness.md
```

Suggested sections:

```markdown
# Textual Default Readiness

## Summary
Decision: go / no-go

## Tested Matrix

## Parity Gaps

## Safety Review

## Non-Interactive Boundary

## Recommendation
```

## Files to Inspect / Modify

Likely:

```text
src/d2c/main.py
src/d2c/tui/
tests/test_tui_textual.py
tests/test_repl_ux.py
tests/test_repl_commands.py
README.md
docs/textual-readiness.md
CHANGELOG.md
```

Optional:

```text
plans/phase77-textual-default-readiness-audit.md
tests/test_phase77_tui_selector.py
```

## Tests

Add or update tests for:

1. `--tui classic` selects prompt-toolkit path.
2. `--tui textual` selects Textual path.
3. CLI selector overrides `D2C_TUI`.
4. Invalid `--tui` value errors clearly.
5. Headless prompt does not start Textual even if `D2C_TUI=textual`.
6. Textual fallback behavior is explicit when dependency is unavailable.

Only add these if the `--tui` selector is implemented in this phase.

## Verification

Fast only:

```bash
./scripts/check_fast.sh
python -m pytest tests/test_tui_textual.py tests/test_repl_ux.py tests/test_repl_commands.py
```

Manual smoke:

```bash
python -m d2c
D2C_TUI=textual python -m d2c
python -m d2c --tui classic
python -m d2c --tui textual
```

Run the audit matrix manually and record results in `docs/textual-readiness.md`.

Do not run the full release gate unless explicitly requested.

## Acceptance Criteria

- Textual readiness audit report exists.
- Audit matrix is filled with pass/fail/notes.
- Default recommendation is explicit.
- Any blocking gaps are documented.
- If `--tui` is added, precedence and fallback are tested.
- No default flip occurs unless go criteria are met.
- Fast checks pass.

## Expected Outcome

The project gets a clear, evidence-based decision on Textual default readiness.
Either Textual becomes a credible default candidate with a documented fallback,
or the remaining gaps are known and prioritized without destabilizing the
existing prompt-toolkit REPL.
