# Phase 79: Textual default with classic fallback

**Priority:** HIGH (switch the default interactive UX after dogfooding fixes)

## Context

Phases 74-78 moved Textual from prototype to usable opt-in UI:

- Phase 74: Textual app shell behind `D2C_TUI=textual`
- Phase 75: approval modal and tool progress timeline
- Phase 76: scrollback, input history, keybindings, resize ergonomics
- Phase 77: `--tui classic|textual|auto` selector and readiness audit
- Phase 78: clickable approval buttons and transcript selection mode

The remaining decision is default behavior. The project currently defaults to
classic prompt-toolkit, with Textual opt-in. Phase 79 should flip the default
interactive UI to Textual while preserving classic as a reliable fallback.

## Goal

Make Textual the default for interactive REPL sessions and keep classic
prompt-toolkit available.

Selection behavior:

1. `--tui classic` always forces classic.
2. `--tui textual` always requests Textual, with clear classic fallback if the
   optional dependency is missing.
3. `--tui auto` uses the project default, now Textual.
4. `D2C_TUI=classic` forces classic when no explicit CLI override is provided.
5. `D2C_TUI=textual` remains supported.
6. Headless/SDK/MCP/eval/server paths remain unchanged.

## Scope

In scope:

- change project default UI from `classic` to `textual`
- preserve `--tui classic`
- preserve `D2C_TUI=classic`
- preserve fallback to classic when Textual is unavailable
- update README and readiness docs
- update selector tests
- changelog note

Out of scope:

- first-run chooser
- persisted UI preference
- removing prompt-toolkit
- changing Textual layout/features
- changing approval/tool semantics
- full release gate

## Required Behavior

### Default Interactive Run

```bash
python -m d2c
```

Expected:

- selects Textual if installed
- falls back to classic with a clear note if Textual is unavailable
- does not affect single-shot/headless prompts

### Explicit Classic

```bash
python -m d2c --tui classic
D2C_TUI=classic python -m d2c
```

Expected:

- always uses prompt-toolkit classic UI
- no Textual app import/start

### Explicit Textual

```bash
python -m d2c --tui textual
D2C_TUI=textual python -m d2c
```

Expected:

- uses Textual when available
- falls back clearly when unavailable

### Headless Boundary

```bash
python -m d2c "hello"
D2C_TUI=textual python -m d2c "hello"
```

Expected:

- no Textual UI starts
- output remains plain/headless

## Design Direction

Likely one-line default flip:

```python
# src/d2c/tui/__init__.py
DEFAULT_UI = "textual"
```

Then update tests and docs that currently state default is classic.

Keep fallback logic in `ui_decision()` unchanged:

```python
if resolve_ui(cli_choice) == "textual":
    return "textual" if available else "classic-fallback"
return "classic"
```

This means default Textual still falls back to classic when the optional
dependency is missing.

## Files to Inspect / Modify

Likely:

```text
src/d2c/tui/__init__.py
tests/test_phase77_tui_selector.py
README.md
docs/textual-readiness.md
CHANGELOG.md
```

Optional:

```text
tests/test_tui_textual.py
plans/phase79-textual-default-classic-fallback.md
```

## Tests

Update or add tests for:

1. `resolve_ui("auto") == "textual"` when no env var is set.
2. `resolve_ui(None) == "textual"` when no env var is set.
3. `D2C_TUI=classic` still resolves classic.
4. CLI `--tui classic` overrides default and env.
5. `ui_decision("auto", available=True) == "textual"`.
6. `ui_decision("auto", available=False) == "classic-fallback"`.
7. Headless still has no Textual code path.

## Verification

Fast only:

```bash
./scripts/check_fast.sh
python -m pytest tests/test_phase77_tui_selector.py tests/test_tui_textual.py
```

Manual smoke:

```bash
python -m d2c
python -m d2c --tui classic
python -m d2c --tui textual
D2C_TUI=classic python -m d2c
python -m d2c "hello"
```

Do not run the full release gate unless explicitly requested.

## Acceptance Criteria

- Interactive `python -m d2c` selects Textual by default when available.
- Classic remains available through `--tui classic` and `D2C_TUI=classic`.
- Textual unavailable fallback is clear and uses classic.
- Non-interactive paths are unchanged.
- Docs/readiness report explain the new default and fallback.
- Fast checks pass.

## Expected Outcome

Textual becomes the default interactive experience without removing the mature
prompt-toolkit REPL. Users get the richer UI by default, while classic remains a
safe fallback for compatibility, missing optional dependencies, or personal
preference.
