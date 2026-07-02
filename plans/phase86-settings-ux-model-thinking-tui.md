# Phase 86: Settings UX for model, thinking, and TUI

**Priority:** HIGH (make recent defaults controllable in-app)

## Context

Recent phases added user-facing choices that matter in every session:

- Phase 79/80: Textual is the default UI, with `ui.default` persisted via
  `/settings ui classic|textual|auto`.
- Phase 81/83: default model is `deepseek-v4-flash`; `pro` remains available.
- Phase 82: DeepSeek thinking is opt-in via `--thinking` / `D2C_THINKING`.

Today, `/settings` shows status and can persist the UI preference, but model and
thinking preferences still require env vars, CLI flags, or hand-editing config.
That makes the new DeepSeek controls harder to discover and use than the UI
preference.

## Goal

Extend the existing `/settings` REPL command into a small, predictable
preference surface for:

```text
ui.default
model
thinking
```

Keep CLI/env overrides first-class and visible so users understand why a setting
may not take effect in the current session.

## Scope

In scope:

- `/settings` status output improvements
- `/settings ui classic|textual|auto` remains supported
- `/settings model flash|pro|auto`
- `/settings thinking off|low|medium|high|auto`
- persistence to the user settings file only
- override/source display for CLI/env/user/default
- completion/help updates
- tests and docs

Out of scope:

- managed/project policy forcing personal UI/model/thinking preferences
- editing permission rules from `/settings`
- editing API keys/secrets from `/settings`
- changing model/thinking defaults
- changing Textual layout
- full release gate

## Precedence

Preserve existing runtime precedence:

```text
CLI > env > user settings > project/default
```

Specifics:

- `--model` wins over `D2C_MODEL` and saved model preference.
- `D2C_MODEL` wins over saved model preference.
- `--thinking` wins over `D2C_THINKING` and saved thinking preference.
- `D2C_THINKING` wins over saved thinking preference.
- `--tui` wins over `D2C_TUI` and saved `ui.default`.
- `D2C_TUI` wins over saved `ui.default`.

Saved preferences should affect future sessions. If changing current-session
state is easy and consistent, update it too, but the minimum behavior is clear
persistence plus a message explaining whether a higher-precedence override is
active.

## Commands

Keep:

```text
/settings
/settings ui
/settings ui classic
/settings ui textual
/settings ui auto
```

Add:

```text
/settings model
/settings model flash
/settings model pro
/settings model auto

/settings thinking
/settings thinking off
/settings thinking low
/settings thinking medium
/settings thinking high
/settings thinking auto
```

Meanings:

- `auto`: remove the user preference and return to env/CLI/default behavior.
- `model flash`: persist `deepseek-v4-flash` preference.
- `model pro`: persist the project's canonical Pro alias/model.
- `thinking off|low|medium|high`: persist the thinking preset.

Do not persist raw custom model IDs in this phase unless validation and UX are
explicitly designed. Keep the command simple: `flash`, `pro`, `auto`.

## Status Output

Bare `/settings` should show compact rows like:

```text
model: deepseek-v4-flash (default)
thinking: off (default)
ui: textual (default)
```

When overridden:

```text
model: deepseek-v4-pro (user preference: pro)
thinking: medium (env: D2C_THINKING)
ui: classic (CLI: --tui)
```

If a saved preference is shadowed by env/CLI, make that visible:

```text
model: deepseek-v4-flash (env: D2C_MODEL, user preference: pro is shadowed)
```

Keep the output readable in both classic and Textual REPLs.

## Persistence

Use the existing user settings file:

```text
~/.d2c/settings.yaml
```

Suggested shape:

```yaml
ui:
  default: textual
model:
  default: deepseek-v4-pro
thinking:
  default: medium
```

If the current settings loader strongly prefers scalar top-level fields, choose
the smallest compatible shape, but document it. Preserve unrelated keys when
writing.

Do not read/write the real user settings path in tests.

## Files to Inspect / Modify

Likely:

```text
src/d2c/main.py
src/d2c/settings.py
src/d2c/config.py
src/d2c/tui/__init__.py
README.md
docs/textual-readiness.md
CHANGELOG.md
tests/test_phase80_tui_preference.py
tests/test_repl_commands.py
```

Optional:

```text
tests/test_phase86_settings_preferences.py
```

## Tests

Add or update tests for:

1. `/settings model flash` persists the Flash preference.
2. `/settings model pro` persists the Pro preference.
3. `/settings model auto` removes the model preference and preserves unrelated
   settings keys.
4. `/settings thinking off|low|medium|high` persists valid presets.
5. `/settings thinking auto` removes the thinking preference.
6. invalid model/thinking values print usage and mutate nothing.
7. bare `/settings` shows model, thinking, and UI values with their source.
8. env overrides shadow user preferences and are reported as such.
9. CLI overrides shadow env/user preferences where testable.
10. default behavior is unchanged when no user preference exists.
11. command registry/help/autocomplete includes the new subcommands.

Use temp settings paths via the existing `isolate_user_settings` fixture.

## Verification

Fast only:

```bash
./scripts/check_fast.sh
python -m pytest tests/test_phase80_tui_preference.py tests/test_repl_commands.py tests/test_phase86_settings_preferences.py
```

Manual smoke:

```text
/settings
/settings model pro
/settings thinking medium
/settings ui classic
/settings
/settings model auto
/settings thinking auto
/settings ui auto
```

Also smoke with env overrides:

```bash
D2C_MODEL=deepseek-v4-flash D2C_THINKING=off D2C_TUI=classic python -m d2c
```

Do not run the full release gate unless explicitly requested.

## Risks

| Risk | Mitigation |
| --- | --- |
| Settings writer corrupts unrelated YAML | Preserve unknown keys; test round-trip writes |
| Source reporting gets confusing | Keep labels simple: CLI/env/user/default |
| User expects immediate current-session mutation | Either update current config or print "applies next session" explicitly |
| Project settings override personal preference unexpectedly | Keep these preferences user-only unless deliberately expanded later |
| Custom model users feel blocked | Raw custom models remain available through CLI/env/config; `/settings model` stays simple |

## Acceptance Criteria

- `/settings` can inspect and persist model, thinking, and UI preferences.
- CLI/env overrides remain higher precedence.
- Shadowed saved preferences are visible in status output.
- Invalid commands are non-mutating and explain valid choices.
- The user settings writer preserves unrelated keys.
- Docs/help/autocomplete are updated.
- Fast checks pass.

## Expected Outcome

Users can control the main day-to-day choices from inside the REPL instead of
remembering env vars or hand-editing YAML. The recent DeepSeek and Textual work
becomes discoverable, reversible, and easier to dogfood.
