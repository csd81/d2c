# Phase 31: Rich TUI / REPL Console (User Experience)

**Paper Reference:** Section 3.2, 3.3 — "Ink framework terminal UI... interactive REPL composition... screens/ components/ compose full-screen layouts, permission dialogs, progress indicators."

**Priority:** HIGH (User Experience)

## Rationale

The interactive mode in `d2c` currently relies on Python's basic `input("> ")` loop. It lacks syntax highlighting, codebase path auto-completion, argument validation, and history searching. 

To improve usability, we will integrate `prompt_toolkit`. This library allows us to build a rich interactive CLI console in Python that mirrors the high-quality developer experience of Claude Code's node-based TUI, featuring file path autocompletion, real-time command highlighting, fuzzy history search, and a formatted bottom status bar.

---

## Files to Create/Modify

1. MODIFY `pyproject.toml` — add `prompt-toolkit` to dependencies
2. MODIFY `src/d2c/main.py` — rebuild `run_interactive` using `prompt_toolkit.PromptSession`
3. CREATE `tests/test_repl_ux.py` — verify REPL session initialization, completions, and status formatting

---

## Key Design

### 1. Adding Dependencies
Add `prompt-toolkit` to the dependencies block in `pyproject.toml`:
```toml
dependencies = [
    "anthropic>=0.39.0",
    "pydantic>=2.0.0",
    "httpx>=0.27.0",
    "pyyaml>=6.0",
    "pymupdf>=1.23.0",
    "tiktoken>=0.7.0",
    "prompt-toolkit>=3.0.40",  # Added for rich interactive console
]
```

### 2. The Auto-Completer (`src/d2c/main.py` or new helper)
We will build a custom `D2CCompleter` that inherits from `prompt_toolkit.completion.Completer` to yield completions for:
1. **Slash Commands**: Autocomplete commands like `/exit`, `/clear`, `/resume`, `/fork`, `/settings`.
2. **File Paths**: Scan the current working directory recursively (with limits) and suggest file names when the user types commands or arguments.
3. **Registered Tools**: Suggest tool names (e.g. `Read`, `Write`, `Glob`, `Grep`, `Agent`).

```python
from prompt_toolkit.completion import Completer, Completion

class D2CCompleter(Completer):
    def __init__(self, cwd: Path, tools: list[str]):
        self.cwd = cwd
        self.tools = tools
        self.commands = ["/exit", "/clear", "/resume", "/fork", "/settings"]

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        # 1. Autocomplete commands
        if text.startswith("/"):
            for cmd in self.commands:
                if cmd.startswith(text):
                    yield Completion(cmd, start_position=-len(text))
            return

        # 2. Autocomplete files if typing a path-like string
        # Resolve files relative to cwd
        ...
```

### 3. Dynamic Status Bar (Bottom Toolbar)
Implement a function that formats a status line displayed at the bottom of the console window:

```python
from prompt_toolkit.formatted_text import HTML

def get_statusbar_text(config: Config, session_store: Any, active_tasks: list) -> HTML:
    """Return statusbar text formatted as HTML."""
    mode = config.permission_mode.upper()
    sess_id = session_store.session_id if session_store else "NONE"
    task_count = len(active_tasks)
    task_str = f" | Tasks: {task_count}" if task_count > 0 else ""
    
    return HTML(
        f"<style bg='ansiblue' fg='ansiwhite'> "
        f"<b>d2c</b> | "
        f"Session: <b>{sess_id}</b> | "
        f"Mode: <b>{mode}</b> | "
        f"Model: {config.model}"
        f"{task_str} "
        f"</style>"
    )
```

### 4. Integration in `run_interactive`
Initialize a `PromptSession` and run the REPL loop:

```python
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory

async def run_interactive(args: argparse.Namespace) -> None:
    config = Config.load(args.cwd)
    # ... setup sessions, plugins, tools ...

    # Setup history file matching Phase 22 global history
    history_file = Path.home() / ".d2c" / "repl_history.txt"
    session = PromptSession(
        history=FileHistory(str(history_file)),
        completer=D2CCompleter(config.cwd, [t.name for t in tools]),
    )

    while True:
        try:
            # Render prompt with status bar
            prompt_text = await session.prompt_async(
                "> ",
                bottom_toolbar=lambda: get_statusbar_text(config, session_store, active_tasks)
            )
            prompt_text = prompt_text.strip()
        except KeyboardInterrupt:
            continue  # Clear current line
        except EOFError:
            break  # Ctrl+D exits

        if not prompt_text:
            continue
        if prompt_text in ("/exit", "/quit"):
            break

        # Run queryLoop...
```

---

## Edge Cases

* **Windows Command Prompt (cmd.exe)**: `prompt_toolkit` falls back to basic console formatting if ANSI escapes are not supported, ensuring it does not crash on legacy Windows consoles.
* **Large Codebases**: Searching for file completions recursively in a huge codebase (e.g. `node_modules` or `venv`) can block the UI. The file completion must explicitly ignore patterns matching `.gitignore` and default to a maximum depth of 2 directories unless searching a specific subfolder.

---

## Tests

Verify the following:
* `test_completer_slash_commands`: Typing `/` yields command completions.
* `test_completer_file_paths`: Typing a partial path returns matching files in the mock workspace.
* `test_statusbar_rendering`: Verifies `get_statusbar_text` returns the correct HTML content with the session ID and active permission modes.
