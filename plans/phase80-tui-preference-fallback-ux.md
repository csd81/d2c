# Phase 80: TUI preference and fallback UX

**Priority:** HIGH (polish the new Textual-by-default behavior)

## Context

Phase 79 made Textual the default interactive UI while preserving classic
prompt-toolkit as an explicit and dependency-missing fallback.

Because Textual is an optional extra, users who install only the base package can
now see a fallback note on every interactive launch. That is correct but may be
noisy. Users also need a clear way to choose classic as their normal preference
without passing `--tui classic` every time.

## Goal

Make the Textual-default transition pleasant and explicit:

1. Improve the missing-Textual fallback message.
2. Provide a way to persist a preferred UI.
3. Preserve all existing override precedence.
4. Never prompt or change behavior in non-interactive paths.

## Scope

In scope:

- clearer fallback messaging
- optional persisted UI preference in user settings
- optional REPL command to set UI preference
- tests for precedence and non-interactive boundaries
- README/docs updates

Out of scope:

- reverting Textual default
- removing classic fallback
- first-run wizard with multiple questions
- changing Textual layout/features
- changing scoped/managed-policy semantics unless needed
- full release gate

## Current Behavior

Selection after Phase 79:

1. CLI `--tui classic|textual|auto`
2. `D2C_TUI=classic|textual`
3. project default: `textual`
4. if Textual unavailable: classic fallback with a note

## Proposed Behavior

Add user preference below CLI/env and above project default:

1. CLI `--tui classic|textual|auto`
2. `D2C_TUI=classic|textual`
3. user setting: `ui.default: classic|textual`
4. project default: `textual`
5. if selected Textual unavailable: classic fallback with a concise note

Example user settings:

```yaml
ui:
  default: classic
```

## Fallback Message

When Textual is selected but unavailable:

```text
Textual UI is the default but the optional [tui] extra is not installed.
Using classic UI for this session.
Install: pip install "d2c[tui]"
Silence this: python -m d2c --tui classic
```

If a user preference is implemented, include:

```text
Persist classic: /settings ui classic
```

Keep the message short. Do not print it in headless/single-shot mode.

## Preference UX

Preferred minimal command:

```text
/settings ui classic
/settings ui textual
/settings ui auto
```

Behavior:

- `classic`: persist classic as user default
- `textual`: persist Textual as user default
- `auto`: remove the user override and use project default/other layers

If modifying `/settings` is too invasive, implement a narrower command:

```text
/tui classic
/tui textual
/tui auto
```

But prefer `/settings ui ...` because it fits the existing command vocabulary.

## Design Notes

Integrate with existing settings infrastructure if practical:

```text
~/.d2c/settings.yaml
```

Do not write project-local settings for this. UI preference is personal.

Do not let project settings force a user UI without explicit design review.
Managed policy may eventually restrict UI choice, but that is out of scope for
Phase 80.

## Files to Inspect / Modify

Likely:

```text
src/d2c/tui/__init__.py
src/d2c/settings.py
src/d2c/main.py
tests/test_phase77_tui_selector.py
tests/test_repl_commands.py
README.md
docs/textual-readiness.md
CHANGELOG.md
```

Optional:

```text
tests/test_phase80_tui_preference.py
```

## Tests

Add or update tests for:

1. CLI `--tui` still wins over env, user setting, and default.
2. `D2C_TUI` wins over user setting and default.
3. user setting wins over project default.
4. absent user setting falls back to project default (`textual`).
5. invalid user setting is ignored with a warning or defaults safely.
6. Textual unavailable still falls back to classic.
7. headless prompt has no TUI preference prompt or Textual launch.
8. `/settings ui classic|textual|auto` persists/removes the preference if the
   command is implemented.

Use temporary settings paths in tests. Do not touch the real `~/.d2c`.

## Verification

Fast only:

```bash
./scripts/check_fast.sh
python -m pytest tests/test_phase77_tui_selector.py tests/test_repl_commands.py
```

Manual smoke:

```bash
python -m d2c
python -m d2c --tui classic
D2C_TUI=classic python -m d2c
```

If settings command is implemented:

```text
/settings ui classic
/settings ui textual
/settings ui auto
```

Do not run the full release gate unless explicitly requested.

## Acceptance Criteria

- Missing-Textual fallback message is concise and actionable.
- Users can persist a preferred UI or at least clearly silence fallback noise.
- Override precedence is documented and tested.
- No non-interactive path prompts or launches Textual.
- Classic remains a first-class fallback.
- Fast checks pass.

## Expected Outcome

Textual remains the default, but users without the optional extra or users who
prefer classic get a clean, low-friction path. The default flip feels intentional
instead of noisy.
