# Phase 72: REPL command UX consolidation

**Priority:** HIGH (small interactive polish with broad daily impact)

## Context

The REPL command surface has grown across many phases:

- session commands (`/clear`, `/resume`, `/fork`, `/exit`)
- status/config commands (`/settings`, `/status`, `/usage`)
- safety/control commands (`/approvals`, `/profiles`)
- diagnostics and helper commands (`/help`, possibly `/doctor`)

Each command works, but the UX has evolved incrementally. `/help`, autocomplete,
unknown-command handling, and output formatting should now be consolidated so
the REPL feels coherent.

## Goal

Make slash commands easier to discover, invoke, and understand.

Primary goals:

1. Group `/help` by workflow.
2. Ensure autocomplete includes every command and common subcommand.
3. Add typo suggestions for unknown commands.
4. Standardize output formatting across command handlers.
5. Keep all default command behavior non-destructive.

## Scope

In scope:

- `/help` rewrite
- command registry or shared command metadata if useful
- prompt-toolkit completer updates
- unknown-command suggestions
- formatting cleanup for command output
- tests for command discovery and suggestions

Out of scope:

- full-screen TUI redesign
- changing command semantics
- adding new feature commands
- command aliases unless strongly justified
- running the full release gate

## Proposed UX

### `/help`

Group commands by intent:

```text
Session
  /clear                 Start a fresh session
  /resume <id>           Resume a saved session
  /fork <id>             Fork a saved session
  /exit                  Exit the REPL

State
  /status                Show session, cwd, model, trust, usage
  /settings              Show active settings
  /usage                 Show token and cost totals

Safety
  /approvals             Show approval cache state
  /approvals reset       Clear persistent approvals
  /profiles              List subagent profiles
  /profiles show <name>  Show profile boundaries

Help
  /help                  Show this help
```

### Unknown Commands

For close typos:

```text
Unknown command: /aprovals
Did you mean /approvals?
```

For no close match:

```text
Unknown command: /whatever
Run /help to see available commands.
```

Use a small standard-library approach such as `difflib.get_close_matches`.

### Autocomplete

Ensure completion covers:

- top-level commands
- common subcommands:
  - `/approvals clear-session`
  - `/approvals reset`
  - `/profiles show`
  - `/profiles doctor`

Do not attempt dynamic profile-name completion unless it is cheap and already
available from the trusted profile loader.

### Output Style

Prefer compact, aligned output:

```text
session:     abc123
cwd:         /path/to/repo
model:       deepseek-v4-pro
trust:       trusted
```

Avoid long explanatory prose inside command output. `/help` should be concise
and scannable.

## Design Direction

If command definitions are currently scattered, introduce a small shared
metadata table in `main.py` or a new helper:

```python
@dataclass(frozen=True)
class SlashCommand:
    name: str
    usage: str
    summary: str
    group: str
    subcommands: tuple[str, ...] = ()
```

Use it for:

- `/help` rendering
- autocomplete
- unknown-command suggestions

Keep the actual handlers where they are unless a small dispatch cleanup is
obviously simpler.

## Files to Inspect / Modify

Likely:

```text
src/d2c/main.py
tests/test_repl_commands.py
tests/test_repl_ux.py
```

Optional:

```text
README.md
CLAUDE.md
CHANGELOG.md
tests/test_phase72_repl_command_ux.py
```

## Tests

Add or update tests for:

1. `/help` includes all known top-level commands.
2. `/help` groups commands under expected headings.
3. Autocomplete includes all top-level commands.
4. Autocomplete includes common subcommands.
5. Unknown typo gets a suggestion.
6. Unknown unrelated command points to `/help`.
7. Command output does not wrap obvious labels awkwardly in narrow formatting
   helpers, where applicable.

## Verification

Fast only:

```bash
./scripts/check_fast.sh
python -m pytest tests/test_repl_commands.py tests/test_repl_ux.py
```

If a new test file is added:

```bash
python -m pytest tests/test_phase72_repl_command_ux.py
```

Do not run the full release gate unless explicitly requested.

## Acceptance Criteria

- `/help` is grouped, complete, and concise.
- Unknown slash-command typos suggest the nearest known command.
- Autocomplete covers every top-level command and common subcommands.
- Output formatting is consistent with recent `/approvals` and `/profiles`
  command style.
- Existing command semantics are unchanged.
- Fast checks pass.

## Expected Outcome

The REPL command surface feels deliberate instead of accumulated. Users can
discover safety, profile, usage, and session controls quickly, and simple typos
lead to helpful recovery instead of dead ends.
