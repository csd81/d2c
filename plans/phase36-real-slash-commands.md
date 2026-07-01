# Phase 36: Real slash command handling

**Paper Reference:** Section 3.2 / 3.3 — interactive REPL command surface.

**Priority:** HIGH (User experience, advertised behavior)

## Context

The REPL advertises slash commands such as `/clear`, `/resume`, `/fork`, `/settings`, and `/help`,
but only `/exit` and `/quit` are meaningfully handled. This creates a mismatch between the UI and
runtime behavior.

This phase makes the advertised commands real without expanding into a full TUI rewrite.

## Goal

Implement first-class REPL slash command handling for:

1. `/help`
2. `/settings`
3. `/clear`
4. `/resume <session_id>`
5. `/fork <session_id>`

Keep `/exit` and `/quit` as existing stop commands.

## Files to Create/Modify

1. MODIFY `src/d2c/main.py`
   - Add a small slash-command dispatcher for interactive mode.
   - Route recognized commands before invoking `queryLoop()`.
   - Keep unknown slash commands user-visible instead of silently sending them to the model.

2. MODIFY `tests/test_repl_ux.py` or CREATE `tests/test_repl_commands.py`
   - Unit-test command parsing and command effects without requiring an interactive terminal.

3. OPTIONAL MODIFY `README.md`
   - Confirm the command list matches implemented behavior.

## Design

Add a parser that returns a structured command:

```python
@dataclass
class SlashCommand:
    name: str
    args: list[str]
```

Parsing rules:

- Trim leading/trailing whitespace.
- Only handle input starting with `/`.
- Split command name from args with shell-like parsing if already available; otherwise use simple
  whitespace splitting.
- Normalize command names to lowercase.

Example:

```python
def parse_slash_command(text: str) -> SlashCommand | None:
    if not text.startswith("/"):
        return None
    parts = text.split()
    return SlashCommand(name=parts[0].lower(), args=parts[1:])
```

Add a dispatcher used only by `run_interactive()`:

```python
async def handle_slash_command(cmd, repl_state) -> bool:
    ...
```

Return value:

- `True` means the command was handled and the REPL should continue.
- `False` means the REPL should exit.

## Command Behavior

### `/help`

Print a concise list of supported commands:

```text
/help                 Show commands
/settings             Show current model, mode, cwd, session
/clear                Start a fresh session
/resume <session_id>  Resume an existing session
/fork <session_id>    Fork an existing session into a new session
/exit, /quit          Exit
```

### `/settings`

Print current runtime settings:

- model
- permission mode
- cwd
- session id
- stream enabled/disabled
- workspace trust state if available

No secrets should be printed.

### `/clear`

Start a fresh conversation/session for subsequent prompts.

Expected effects:

- create a new session store
- reset in-memory conversation state
- keep the same cwd, model, tools, config, and permission mode
- show the new session id

### `/resume <session_id>`

Load an existing session transcript and continue from it.

Expected effects:

- validate that a session id was provided
- reconstruct messages through `SessionStore.reconstruct_messages()`
- replace the active session store
- replace the in-memory conversation state
- do not restore prior permission grants
- show the resumed session id

### `/fork <session_id>`

Create a new session branched from an existing session.

Expected effects:

- validate that a session id was provided
- call existing fork/session-manager logic
- reconstruct messages into the new active session
- do not copy compact-boundary entries unless the existing fork helper already handles that
- do not restore prior permission grants
- show source and new session ids

## State Model

Interactive mode needs explicit mutable REPL state:

```python
@dataclass
class ReplState:
    config: Config
    session_store: SessionStore
    messages: list[dict]
    tools: list[Tool]
    hooks: HookManager
    permission_engine: PermissionEngine
```

If `run_interactive()` currently builds a fresh single-turn message list for each prompt, this phase
should change it to preserve conversation messages across prompts. `/clear`, `/resume`, and `/fork`
then become simple state replacement operations.

## Edge Cases

- `/resume` with no id: print usage and keep current session.
- `/fork` with no id: print usage and keep current session.
- unknown session id: print a clear error and keep current session.
- unknown slash command: print `Unknown command: /name`.
- `/clear` during a session with pending background tasks: keep this phase simple and do not cancel
  background work unless a background manager is already wired.
- malformed command arguments: print usage, do not call the model.

## Tests

Add tests that assert:

1. `parse_slash_command("/help")` returns `help` with no args.
2. Unknown slash commands are handled locally and are not sent to `queryLoop()`.
3. `/settings` prints model, permission mode, cwd, and session id without secrets.
4. `/clear` replaces the active session and empties in-memory conversation state.
5. `/resume <id>` loads reconstructed messages and replaces the active session.
6. `/fork <id>` creates a new session and loads forked messages.
7. `/resume` and `/fork` with missing ids print usage and preserve current state.

## Verification

Run:

```bash
pytest tests/test_repl_ux.py
pytest tests/test_repl_commands.py
```

Then manually verify:

```bash
python -m d2c
```

Inside the REPL:

```text
/help
/settings
/clear
/resume <known-session-id>
/fork <known-session-id>
/quit
```

## Acceptance Criteria

- Every slash command advertised by the completer has real behavior.
- Unknown slash commands are not sent to the model.
- `/clear`, `/resume`, and `/fork` update the active session used by later prompts.
- Permission grants are not restored across `/resume` or `/fork`.
- Tests cover parser behavior and state-changing commands.
