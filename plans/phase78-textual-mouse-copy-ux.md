# Phase 78: Textual mouse and copy UX

**Priority:** HIGH (default-readiness blocker)

## Context

Manual Textual dogfooding found two important UX gaps:

1. Text selection is difficult because the Textual app captures mouse input.
2. The permission approval modal is keyboard-first; approval choices are not
   clickable even though users naturally expect modal actions to work by mouse.

Phase 77 correctly kept Textual opt-in. These issues should be treated as
default-readiness blockers before any future default flip.

## Goal

Make the Textual UI usable with normal terminal mouse/copy workflows and make
permission decisions clickable without changing approval semantics.

Primary goals:

1. Permission modal choices are real clickable controls.
2. Keyboard shortcuts for approval remain unchanged.
3. Transcript text can be selected or copied through a documented UX.
4. `docs/textual-readiness.md` records the manual finding and resolution.

## Scope

In scope:

- Textual approval modal button controls
- mouse click handling for `[y]`, `[a]`, `[A]`, `[n]`
- copy/selection strategy for transcript content
- docs update for terminal selection behavior
- Textual pilot tests for button clicks where practical

Out of scope:

- making Textual default
- changing approval scopes
- broad clipboard integration across every platform
- mouse-first redesign of the whole UI
- rich text editor behavior
- full release gate

## Approval Modal UX

Use Textual `Button` widgets or equivalent clickable controls.

Required buttons:

```text
Deny    Once    Session    Always
```

Mapping:

| Button | Key | Meaning |
|---|---|---|
| Deny | `n` / Esc / default | deny |
| Once | `y` | approve once |
| Session | `a` | approve exact action for current session |
| Always | `A` | persist exact approval |

Requirements:

- Deny remains default.
- Existing keyboard shortcuts still work.
- Unknown keys still deny or do nothing safely according to current behavior.
- Choice still flows through the existing approval application path.
- No duplicate approval semantics in Textual-specific code.

## Text Selection / Copy UX

Textual mouse handling can interfere with normal terminal drag selection.
Provide one clear solution.

Acceptable options:

### Option A: Selection Mode

Add a toggle such as:

```text
Ctrl+S  Toggle selection mode
```

When selection mode is enabled:

- mouse capture is reduced/disabled where Textual allows it
- transcript content remains visible
- status bar indicates selection mode
- normal terminal selection works, or the mode clearly tells users to use
  Shift+drag if the terminal requires it

### Option B: Copy Focused Block

Add keyboard copy support:

```text
c       Copy focused transcript block
Ctrl+C  unchanged existing behavior unless focus/copy mode says otherwise
```

This requires a safe clipboard dependency or terminal-compatible approach. If
clipboard support is platform-fragile, prefer Option A for v1.

### Option C: Plain Transcript Export

Add an explicit command:

```text
/copy-last
/transcript path
```

This is useful but less direct than selection mode. Consider it a fallback if
Textual cannot expose selection well.

Recommended v1: **Option A**, plus documentation that many terminals support
`Shift+drag` selection even when mouse reporting is active.

## Documentation

Update `docs/textual-readiness.md`:

- record the manual dogfooding finding
- mark permission click and text selection as blockers before the fix
- mark resolution after implementation
- keep Textual opt-in unless all manual blockers are cleared

Update README Textual section with:

- clickable approvals
- selection/copy instructions
- any terminal-specific caveat such as Shift+drag

## Files to Inspect / Modify

Likely:

```text
src/d2c/tui/approvals.py
src/d2c/tui/app.py
src/d2c/tui/widgets.py
tests/test_tui_textual.py
docs/textual-readiness.md
README.md
CHANGELOG.md
```

Optional:

```text
tests/test_phase78_textual_mouse_copy.py
```

## Tests

Add or update tests for:

1. Approval modal contains four button choices.
2. Clicking Deny maps to deny.
3. Clicking Once maps to approve-once.
4. Clicking Session maps to session approval.
5. Clicking Always maps to persistent approval.
6. Keyboard shortcuts still map exactly as before.
7. Selection/copy mode toggles state and updates status/help text.
8. Default prompt-toolkit REPL remains unchanged.

Use Textual pilot click/key helpers where possible. Avoid screenshot tests.

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

- trigger a permission modal
- click each approval button
- verify `[n]`, `[y]`, `[a]`, `[A]` semantics
- attempt terminal text selection
- test Shift+drag fallback if needed
- verify selection/copy instructions are discoverable

Do not run the full release gate unless explicitly requested.

## Acceptance Criteria

- Approval modal choices are clickable.
- Approval keyboard behavior is unchanged.
- Deny remains default.
- Text selection/copy has a documented and tested UX.
- Readiness doc records the blocker and resolution.
- Textual remains opt-in.
- Fast checks pass.

## Expected Outcome

The Textual UI removes two practical blockers found during live dogfooding:
mouse approval actions and transcript selection/copy. This moves Textual closer
to default readiness without changing the mature prompt-toolkit fallback.
