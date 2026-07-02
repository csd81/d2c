# Textual Default Readiness

Audit of the opt-in Textual UI (Phases 74–76) against the default prompt_toolkit
REPL, to decide whether Textual should become the default interactive UI.

## Summary

**Decision (Phase 77): NO-GO. Revised (Phase 79): GO — Textual is now the default,
with classic as the fallback.**

The Phase 77 audit found functional parity and no safety/isolation regressions;
the one blocking gap was real-world dogfooding. Dogfooding then surfaced two
practical blockers — keyboard-only approvals and mouse-capture interfering with
text selection — which **Phase 78 resolved** (clickable approval buttons + a
`Ctrl+S` selection mode). With those cleared, **Phase 79 flips the default**:

- `python -m d2c` uses Textual when the optional `[tui]` extra is installed, and
  falls back to classic with a clear note when it isn't.
- `--tui classic` and `D2C_TUI=classic` always force classic (and print no note).
- Non-interactive paths (headless / `--json` / SDK / MCP / eval / server) are
  unchanged and never start Textual.

The flip is a one-line `DEFAULT_UI = "textual"` change in `d2c/tui/__init__.py`;
the selector precedence and fallback (Phase 77) are unchanged.

**Phase 80** adds a personal preference layer so the default flip isn't noisy:
`/settings ui classic|textual|auto` persists `ui.default` in
`~/.d2c/settings.yaml` (`auto` clears it). Precedence is now `--tui` > `D2C_TUI`
> user `ui.default` > project default (`textual`). The preference is read from
the USER settings file only — a project/managed file cannot force a user's UI.
The missing-`[tui]` fallback note is shortened and lists how to install, use
`--tui classic`, or persist classic.

## Tested Matrix

Legend: ✅ verified (automated/pilot) · 🟡 verified by code review only ·
⬜ needs manual live smoke.

| Area | prompt_toolkit | Textual | Notes |
|---|---|---|---|
| Startup (trusted/untrusted) | ✅ | 🟡 | Textual reuses the same `run_interactive` setup (session/trust/pool/hooks) before the UI branch. |
| Missing API key | ✅ | 🟡 | The loop yields an error `TextResponse`; the app renders it, doesn't crash. |
| Basic loop + Markdown | ✅ | ✅ | Pilot: a turn renders assistant Markdown + a final segment (`test_tui_textual`). |
| Slash commands | ✅ | ✅ | Same registry; handlers' stdout is captured into the transcript. `/help` capture verified via pilot. |
| Unknown-command suggestion | ✅ | ✅ | Shared `parse_slash_command`/`handle_slash_command` (Phase 72). |
| Tool rows (ok/error/denied) | ✅ | ✅ | `tool_row_from_event`/`tool_row_status` unit-tested; distinct styling. |
| Approvals (y/a/A/n, deny default) | ✅ | ✅ | Modal maps to the exact Phase 52/64/65 scopes; pilot confirms `[A]` persists and `Esc` denies. Phase 78: also clickable buttons. |
| Mouse / copy | ✅ (native) | ✅ | Phase 78: clickable approval buttons; `Ctrl+S` selection mode + Shift+drag guidance. |
| Redaction / no speculative read | ✅ | ✅ | `approval_view` reuses `_diff_preview`/`_tool_input_preview` + `observability.redact`. |
| Status footer | ✅ | ✅ | model/mode/trust/cwd/usage/tasks via `status_line`; truncates on small terminals. |
| Keyboard (history/scroll/Ctrl+L/Ctrl+C/Esc) | ✅ | ✅ | Pilot: Up/Down history, scroll-follow, End-to-latest, Ctrl+L view-only clear, Esc-closes-modal. |
| Resize / small terminal | ✅ | ✅ | Pilot mounts at `(20, 5)` without crashing. |
| Headless / `--json` / SDK / MCP / eval / server | ✅ | n/a | Textual never imported/started on these paths (see boundary section). |

## Parity Gaps

- **Live-terminal dogfooding (blocking).** All Textual verification here is via
  `run_test` pilot, not a human using it against a real model over a real TTY.
  The manual smoke matrix in `plans/phase77-*.md` must be run before a flip.
- **Mouse approval + text selection (found in dogfooding; RESOLVED in Phase 78).**
  Live use surfaced two practical blockers: approval choices were keyboard-only,
  and Textual's mouse capture made drag-selecting/copying transcript text hard.
  Phase 78 added clickable `Deny / Once / Session / Always` buttons to the modal
  (same scopes, keyboard unchanged, deny still default) and a `Ctrl+S` selection
  mode that pauses mouse capture (best-effort) and tells the user to Shift+drag.
  No longer blocking.
- **Streaming feel (minor).** The Textual path buffers each assistant segment and
  renders once (same trade-off as the Phase 73 classic renderer); no live
  token-by-token streaming. Acceptable, not a blocker.
- **Ctrl+C mid-run (by design).** Ctrl+C clears input / exits but does not
  interrupt an in-flight tool/turn — there's no safe cancellation path in the
  loop yet. Same limitation as classic. Would be a follow-up, not a blocker.
- **Bold/italic inline Markdown.** Rendered by Rich in Textual; the classic
  renderer leaves `**` literal. This is Textual being *better*, not a gap.

## Safety Review

- Approval **semantics live in one place**: the modal only collects a choice;
  `ApprovalCache.apply_choice` decides. `[y]`/`[a]`/`[A]`/`[n]` scopes and
  **deny-by-default** (Escape/Enter/unknown) are identical to classic.
- Previews are built **only from the tool input already provided** — Bash risk
  via the `acceptEdits` classifier, Edit/Write/ApplyPatch diffs from
  `old_string`/`new_string`/`patch` — never a speculative disk read. Every value
  is routed through `observability.redact`. Persistent approval hashes are never
  shown.
- The permission **engine and deny rules are untouched**; the Textual path only
  swaps the ASK *presentation* (a modal instead of stdin), via a holder set on
  the app's `on_mount`.

## Non-Interactive Boundary

- `import d2c.tui` and `import d2c.main` do **not** import `d2c.tui.app` (the
  only Textual-importing module) — verified in a clean subprocess
  (`test_tui_textual`). Textual is imported lazily, only by `run_textual_app`.
- `run_headless` contains **no** Textual/`resolve_ui`/`ui_decision`/
  `run_textual_app` reference (guarded structurally by
  `test_phase77_tui_selector`), so a headless prompt never starts Textual even
  with `D2C_TUI=textual`.
- SDK / MCP / eval / server do not go through `run_interactive` and are unchanged.
- `textual` is an **optional** extra (`pip install "d2c[tui]"`); the default
  install stays lean.

## Selector & Fallback (shipped this phase)

`--tui classic|textual|auto` with precedence **CLI `--tui` > `D2C_TUI` > project
default (`classic`)**:

| Value | Behavior |
|---|---|
| `classic` | force prompt_toolkit |
| `textual` | force Textual; if the extra isn't installed, print a clear note and fall back to classic |
| `auto` (default) | honor `D2C_TUI`, else classic |

Fallback is unit-tested via `ui_decision(..., available=False) == "classic-fallback"`,
and precedence via `resolve_ui`. No default flip occurred.

## Recommendation

**Done (Phase 79): Textual is the default**, with classic as a first-class
fallback via `--tui classic` / `D2C_TUI=classic` and automatically when the
`[tui]` extra is absent. prompt_toolkit is not removed and stays fully
supported. If further live use surfaces regressions, `--tui classic` is the
immediate escape hatch and reverting the default is again a one-line
`DEFAULT_UI` change.
