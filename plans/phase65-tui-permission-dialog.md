# Phase 65: Interactive TUI permission dialog

**Goal:** Replace the bare plain-text `Allow? [y/N/a]: ` prompt with a rich,
color-coded terminal dialog. Bash commands get risk-colored highlights, edit
operations get inline diff previews, and the scope picker offers
once/session/persistent in a compact visual layout.

No new dependencies — everything uses `prompt_toolkit` (already a dependency)
for styled output and inline formatting.

## Current state

`_permission_prompt_lines()` returns three plain strings:

    Permission required: Bash [shell]
      Reason: uncertain
      Input:  {"command": "rm -rf /tmp/x"}

Then a bare `input("  Allow? [y/N/a]: ")`. It works, but it's hard to scan
quickly, the input shape is JSON rather than human-readable, and there's no
visual risk signal.

## What's changing

### 1. `src/d2c/main.py` — new `_render_permission_dialog()`

Replace `_permission_prompt_lines()` with a function that builds a styled
dialog using `prompt_toolkit`'s `print_formatted_text()` and `HTML`:

**Tool icon/name line:** a styled header with the tool name and category
badge (color-coded: green for READ, yellow for SHELL, red for WRITE).

**Input preview:** formatted per tool type instead of raw JSON:
- **Bash:** show the command string directly. Color the line based on risk
  classification: green (read: `ls`, `cat`, `grep`), yellow (write:
  `sed -i`, `mv`), red (dangerous: `rm -rf`, `sudo`, pipes). Use the
  existing classifier fast-filter from `permissions/classifier.py` to
  determine color.
- **Edit/Write/ApplyPatch:** show a compact unified diff line count
  (`+N / -M`) and the file path. If the diff is short (under 10 lines),
  show it inline with `+` lines in green and `-` lines in red.
- **WebFetch/WebSearch:** show the URL or summarized query.
- **Other tools:** compact JSON preview (existing `_tool_input_preview`).

**Scope line:** a compact row showing the available actions with styled keys:

    [y] once  [a] session  [A] always  [d] diff  [n] deny

The prompt remains a single `input()` line below the formatted output
(using `[y/N/a/A/d]` — default deny, via `asyncio.to_thread`). If the user
types `d`, the dialog re-renders with the expanded diff and re-prompts.

Cached approvals emit a single styled line instead of the full dialog:

    ✓ Bash — approved (cached)

### 2. `src/d2c/main.py` — update `make_interactive_approval()`

The `_permission_prompt_lines()` calls inside `make_interactive_approval`
are replaced with the new `_render_permission_dialog()`. The lock and
recheck logic stays identical. The difference: the old function returned
strings to `print()`, the new one writes directly with
`print_formatted_text(HTML(...))`.

The `interactive_approval()` async helper (used in headless fallback paths)
keeps its plain-text behavior — it's a simpler code path that doesn't need
TUI polish.

### 3. `tests/test_phase65_ui_dialog.py`

New test file:
- Dialog shows tool name, category color hint, and input preview
- Bash commands get risk-colored output based on command heuristic
- Edit operations show file path and +/- line counts
- `d` (diff) re-renders with expanded content
- Secrets are still redacted (via `observability.redact()`) in all output
- Default is still deny
- `y` / `a` / `A` each work correctly (once / session / persistent)
- Cached approval shows compact message and skips the full dialog

### 4. `COMPARISON.md` and `CHANGELOG.md`

Note that Phase 65 added a TUI permission dialog with risk-colored tool
previews and interactive scope selection.

## What's intentionally not changing

- No new Python dependencies (prompt_toolkit already ships with d2c).
- The `interactive_approval()` function stays as a plain-text fallback for
  headless/MCP/pipe modes where styled output isn't appropriate.
- The permission engine, classifier, cache, and audit events are untouched.
- The `diff` action only shows the stored tool input, not a real on-disk
  diff (that would require reading files, which the permission gate must not
  do speculatively).
- No interactive selection widgets (no questionary / inquirer) — a styled
  `input()` prompt is sufficient and avoids another dependency.
- Phase 57's secret redaction path is preserved and tested.

## Risk

Low. The dialog is purely cosmetic — it prints formatted text instead of
plain text. The async lock, cache, and permission decision logic are
unchanged. The `d` diff expansion adds a re-render loop that is
straightforward to test. No new data flows are introduced.

## Design notes

The color heuristic for Bash commands is intentionally simple — the existing
classifier's fast-filter can already classify commands as safe or risky. We
reuse that signal for the color hint rather than duplicating the logic. The
heuristic only needs three buckets:

- **Read** (green): `ls`, `cat`, `head`, `tail`, `grep`, `find`, `echo`,
  `which`, `file`, `stat`, `du`, `df`, `date`, `env`, `printenv`, `pwd`,
  `readlink`, `realpath`, `basename`, `dirname`
- **Write** (yellow): `sed -i`, `mv`, `cp`, `mkdir`, `touch`, `chmod`,
  `chown`, `ln`, `tar`, `gzip`, `gunzip`
- **Dangerous** (red): `rm`, `sudo`, pipe to shell, interpreter `-c`,
  redirect to file, `dd`, `mkfs`, `fdisk`, `kill`, `pkill`
