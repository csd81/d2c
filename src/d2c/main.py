"""Entry point — CLI dispatch.

Paper: All entry surfaces converge on the same agent loop.
Interactive CLI and headless (claude -p equivalent) both feed queryLoop().
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

# Phase 31: Rich TUI / REPL Console
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory

from d2c.compact import CompactConfig
from d2c.config import Config
from d2c.context import (
    assembleMessages,
    getSystemContext,
    getSystemPrompt,
    getUserContext,
)
from d2c.file_history import FileHistory as SessionFileHistory
from d2c.file_history import FileHistoryTracker
from d2c.history import PromptHistory
from d2c.hooks import HookDefinition, HookEvent, HookRegistry, HookType
from d2c.loop import LoopConfig, StopEvent, TextDelta, ToolExecutionEvent, queryLoop
from d2c.loop import TextResponse as LoopTextResponse
from d2c.memory import LazyMemoryLoader
from d2c.observability import AuditLogger, audit, set_audit_logger
from d2c.permissions import PermissionEngine
from d2c.persistence import SessionEntry, SessionManager, SessionStore, _utc_now
from d2c.plugins.loader import PluginLoader
from d2c.sandbox import SandboxConfig
from d2c.tools import set_active_hooks, set_active_memory_loader, set_file_history_tracker
from d2c.tools.pool import Config as PoolConfig
from d2c.tools.pool import assembleToolPool

if TYPE_CHECKING:
    from d2c.trust import WorkSpaceTrustGate


def parse_args() -> argparse.Namespace:
    from d2c import __version__

    parser = argparse.ArgumentParser(description="d2c — an interactive CLI coding agent")
    parser.add_argument(
        "--version",
        action="version",
        version=f"d2c {__version__}",
        help="Print the version and exit",
    )
    parser.add_argument("prompt", nargs="?", help="Single-shot prompt (omit for interactive REPL)")
    parser.add_argument(
        "--model", default=None, help="DeepSeek model to use (v4-pro, chat/v3, reasoner/r1)"
    )
    parser.add_argument("--max-turns", type=int, default=25, help="Maximum agent turns")
    parser.add_argument("--cwd", type=Path, default=None, help="Working directory")
    parser.add_argument("--session", default=None, help="Session ID to use")
    parser.add_argument("--resume", default=None, help="Session ID to resume")
    parser.add_argument("--fork", default=None, help="Session ID to fork from")
    parser.add_argument(
        "--mcp",
        action="store_true",
        help="Start as an MCP server (stdio JSON-RPC) for IDE integration",
    )
    parser.add_argument(
        "--list-models", action="store_true", help="List available DeepSeek models and exit"
    )
    parser.add_argument(
        "--rewind-files",
        default=None,
        metavar="SESSION_ID",
        help="Revert all filesystem changes from the given session",
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="Run local diagnostics (config/env checks) and exit",
    )
    parser.add_argument(
        "--doctor-live",
        action="store_true",
        help="With --doctor: also make a small live WebSearch probe",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="With --doctor: machine-readable JSON output",
    )

    # Trust gate flags (mutually exclusive)
    trust_group = parser.add_mutually_exclusive_group()
    trust_group.add_argument(
        "--trust",
        action="store_true",
        help="Trust the workspace and load all project-local features",
    )
    trust_group.add_argument(
        "--no-trust",
        action="store_true",
        help="Run without trusting the workspace (skip project-local .env, plugins, skills, MCP, memory)",
    )

    return parser.parse_args()


def _setup_session(
    args: argparse.Namespace, config: Config
) -> tuple[SessionStore | None, list[dict] | None]:
    """Create/resume/fork session. Returns (store, resume_messages_or_None)."""
    manager = SessionManager()
    cwd = args.cwd or config.cwd

    if args.resume:
        store, messages = manager.resume_session(args.resume, cwd)
        print(f"Session: {store.session_id} (resumed from {args.resume})")
        return store, messages
    elif args.fork:
        store = manager.fork_session(args.fork, cwd)
        print(f"Session: {store.session_id} (forked from {args.fork})")
        return store, None
    elif args.session:
        store = SessionStore(manager.base_dir, args.session, cwd)
        store.append(
            SessionEntry(
                role="system",
                content="",
                timestamp=_utc_now(),
                entry_type="message",
                metadata={"event": "session_start", "cwd": str(cwd)},
            )
        )
        print(f"Session: {store.session_id}")
        return store, None
    else:
        store = manager.create_session(cwd)
        print(f"Session: {store.session_id}")
        return store, None


def _load_plugins(
    config: Config,
    hook_registry: HookRegistry,
) -> tuple[list, list[dict]]:
    """Load plugins from all sources and register hooks/skills/agents.

    Paper Section 6: "Hook sources include settings.json, plugins, and
    managed policy at startup; skill hooks register dynamically on invocation."

    Returns (plugin_skills, plugin_agents) for tool registration.
    Plugin hooks are registered directly into the provided HookRegistry.
    """
    loader = PluginLoader()
    loaded = loader.discover_and_load(config.cwd)

    plugin_skills: list = []
    plugin_agents: list[dict] = []

    for plugin in loaded:
        if not plugin.is_valid:
            for err in plugin.errors:
                print(f"Warning: Plugin '{plugin.manifest.name}': {err}", file=sys.stderr)
            continue

        # Register hooks
        for hook_def in plugin.manifest.hooks:
            try:
                event = HookEvent(hook_def["event"])
                definition = HookDefinition(
                    event=event,
                    hook_type=HookType(hook_def.get("type", "command")),
                    command=hook_def.get("command"),
                    prompt=hook_def.get("prompt"),
                    source=f"plugin:{plugin.manifest.name}",
                    timeout_ms=hook_def.get("timeout", 30_000),
                )
                hook_registry.register(definition)
                plugin.hooks_registered += 1
            except (ValueError, KeyError) as e:
                print(
                    f"Warning: Plugin '{plugin.manifest.name}': invalid hook definition: {e}",
                    file=sys.stderr,
                )

        # Load skills from plugin directory
        if plugin.manifest.skills:
            from d2c.skills.loader import SkillDefinition, parse_frontmatter

            plugin_dir = Path(plugin.manifest.source_path)
            for skill_file_name in plugin.manifest.skills:
                skill_path = plugin_dir / skill_file_name
                if skill_path.exists() and skill_path.suffix == ".md":
                    try:
                        frontmatter, body = parse_frontmatter(
                            skill_path.read_text(encoding="utf-8")
                        )
                        skill_def = SkillDefinition(
                            name=skill_path.stem,
                            description=frontmatter.get("description", ""),
                            prompt=body,
                            args_schema=frontmatter.get("args"),
                            source=f"plugin:{plugin.manifest.name}",
                        )
                        plugin_skills.append(skill_def)
                        plugin.skills_loaded += 1
                    except OSError as e:
                        print(
                            f"Warning: Plugin '{plugin.manifest.name}': "
                            f"cannot read skill '{skill_file_name}': {e}",
                            file=sys.stderr,
                        )
                else:
                    print(
                        f"Warning: Plugin '{plugin.manifest.name}': "
                        f"skill file not found: {skill_file_name}",
                        file=sys.stderr,
                    )

        # Collect agent definitions
        if plugin.manifest.agents:
            plugin_dir = Path(plugin.manifest.source_path)
            for agent_file_name in plugin.manifest.agents:
                agent_path = plugin_dir / agent_file_name
                if agent_path.exists():
                    plugin_agents.append(
                        {
                            "name": agent_path.stem,
                            "path": str(agent_path),
                            "source": f"plugin:{plugin.manifest.name}",
                        }
                    )
                    plugin.agents_loaded += 1

    return plugin_skills, plugin_agents


# ── Phase 31: Rich REPL components ────────────────────────────────────


class D2CCompleter(Completer):
    """Auto-completer for the interactive REPL.

    Yields completions for:
    1. Slash commands: /exit, /clear, /resume, /fork, /settings, /help
    2. File paths: scans cwd with depth limit, respecting common ignore patterns
    3. Registered tool names (Read, Write, Bash, Glob, Grep, etc.)
    """

    _IGNORE_PATTERNS = {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".pytest_cache",
        ".mypy_cache",
        ".tox",
        "dist",
        "build",
        ".egg-info",
        ".d2c",
        ".hg",
        ".svn",
    }
    _MAX_DEPTH = 2

    def __init__(self, cwd: Path, tools: list[str]):
        self.cwd = cwd
        self.tools = tools
        self.commands = [
            "/exit",
            "/quit",
            "/clear",
            "/resume",
            "/fork",
            "/settings",
            "/help",
        ]

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor

        # Slash commands
        if text.startswith("/"):
            for cmd in self.commands:
                if cmd.startswith(text):
                    yield Completion(cmd, start_position=-len(text))
            return

        # File path completions — scan cwd for matching paths
        yield from self._file_completions(text)

        # Tool name completions — suggest when typing at start or after space
        yield from self._tool_completions(text)

    def _file_completions(self, text: str):
        """Yield file path completions matching the typed prefix."""
        import os

        try:
            # Strip trailing separators from text for prefix matching
            clean_text = text.rstrip("/\\")
            start_path = self.cwd
            prefix = ""
            if clean_text:
                # If text contains a path separator, resolve the directory portion
                if "/" in text or "\\" in text:
                    candidate = (self.cwd / clean_text).resolve()
                    try:
                        candidate.relative_to(self.cwd)
                    except ValueError:
                        return  # Path escapes cwd
                    # If the resolved path is an existing directory, use it
                    if candidate.is_dir():
                        start_path = candidate
                        prefix = ""
                    else:
                        # Parent is the directory, candidate is the prefix
                        parent = candidate.parent
                        if parent.exists() and parent.is_dir():
                            try:
                                parent.relative_to(self.cwd)
                                start_path = parent
                                prefix = candidate.name
                            except ValueError:
                                return
                        else:
                            return
                else:
                    prefix = clean_text

            for entry in sorted(os.listdir(str(start_path))):
                if entry in self._IGNORE_PATTERNS:
                    continue
                if prefix and not entry.lower().startswith(prefix.lower()):
                    continue
                full = start_path / entry
                # Compute display path relative to cwd
                if start_path == self.cwd:
                    display = entry
                else:
                    rel = start_path.relative_to(self.cwd)
                    display = str(rel / entry)
                if full.is_dir():
                    yield Completion(
                        display + os.sep,
                        start_position=-len(text),
                        display_meta="dir",
                    )
                else:
                    yield Completion(
                        display,
                        start_position=-len(text),
                        display_meta="file",
                    )
        except OSError:
            return

    def _tool_completions(self, text: str):
        """Yield tool name completions."""
        if not text:
            return
        # Only suggest tools when typing something that looks like a sentence start
        parts = text.split()
        # Suggest tool names for the first word or after common connecting words
        last_word = parts[-1] if parts else text
        if len(last_word) < 2:
            return
        for tool in self.tools:
            if tool.lower().startswith(last_word.lower()):
                yield Completion(
                    tool,
                    start_position=-len(last_word),
                    display_meta="tool",
                )


def get_statusbar_text(
    config: "Config",
    session_store: Any,
    active_tasks: int = 0,
) -> HTML:
    """Return status bar text formatted as HTML for the bottom toolbar.

    Displays session ID, permission mode, model, and active task count.
    """
    mode = getattr(config, "permission_mode", "default").upper()
    sess_id = ""
    if session_store is not None:
        sess_id = getattr(session_store, "session_id", "") or ""
    task_str = f" | Tasks: {active_tasks}" if active_tasks > 0 else ""

    return HTML(
        f"<style bg='ansiblue' fg='ansiwhite'>"
        f" <b>d2c</b> | "
        f"Session: <b>{sess_id}</b> | "
        f"Mode: <b>{mode}</b> | "
        f"Model: {config.model}"
        f"{task_str} "
        f"</style>"
    )


def _install_file_history(config: Config, session_store) -> None:
    """Point the global file-history tracker at a session (Phase 34/36)."""
    if session_store is not None:
        base_dir = Path.home() / ".d2c"
        file_history = SessionFileHistory(base_dir, session_store.session_id, cwd=config.cwd)
        set_file_history_tracker(FileHistoryTracker(file_history))


async def _wire_runtime(config: Config, session_store, hook_registry) -> None:
    """Phase 34: connect the built-but-inert runtime subsystems.

    - File-history tracker so Write/Edit checkpoint and --rewind-files works.
    - Active hooks accessor so tools (e.g. Task tools) can fire lifecycle events.
    - Active memory loader so file access surfaces nested CLAUDE.md / path rules.
    - Fire the SessionStart hook.
    """
    _install_file_history(config, session_store)
    set_active_hooks(hook_registry)
    set_active_memory_loader(LazyMemoryLoader(config.cwd))
    # Phase 44: initialize audit logging + correlation context.
    logger = AuditLogger.from_config(config)
    set_audit_logger(logger)
    logger.set_context(
        session_id=(session_store.session_id if session_store else None),
        cwd=str(config.cwd),
        model=config.model,
        permission_mode=config.permission_mode,
    )
    audit("session_start", session_id=(session_store.session_id if session_store else None))
    await hook_registry.fire(
        HookEvent.SESSION_START,
        {
            "session_id": session_store.session_id if session_store else None,
            "cwd": str(config.cwd),
            "model": config.model,
            "mode": config.permission_mode,
        },
    )
    # Phase 40: CLAUDE.md / memory hierarchy is loaded into user context here.
    await hook_registry.fire(
        HookEvent.INSTRUCTIONS_LOADED,
        {
            "session_id": session_store.session_id if session_store else None,
            "cwd": str(config.cwd),
        },
    )


def _pool_config_from(config: Config) -> "PoolConfig":
    """Build the tool-pool config, wiring sandbox settings (Phase 34)."""
    return PoolConfig(
        cwd=config.cwd,
        sandbox_config=SandboxConfig(enabled=config.sandbox_enabled),
    )


# ── Phase 36: REPL slash commands ─────────────────────────────────────


@dataclass
class SlashCommand:
    """A parsed REPL slash command."""

    name: str
    args: list[str] = field(default_factory=list)


@dataclass
class ReplState:
    """Mutable REPL state that slash commands operate on."""

    config: Config
    session_store: object
    conversation: list[dict] = field(default_factory=list)
    stream: bool = True


def parse_slash_command(text: str) -> "SlashCommand | None":
    """Parse `/name arg...` into a SlashCommand, or None if not a slash command."""
    text = text.strip()
    if not text.startswith("/"):
        return None
    parts = text.split()
    return SlashCommand(name=parts[0].lower(), args=parts[1:])


def _tool_input_preview(tool_input: dict) -> str:
    """Compact, non-sensitive preview of tool input for the approval prompt."""
    import json

    try:
        s = json.dumps(tool_input, default=str)
    except Exception:
        s = str(tool_input)
    return s[:200] + ("…" if len(s) > 200 else "")


async def interactive_approval(request, result) -> bool:
    """Phase 43: prompt the user to approve/deny an ASK tool request.

    Default is deny (empty input). `y`/`yes` approves once. Runs input() off
    the event loop so streaming isn't blocked.
    """
    reason = getattr(result, "reason", "") or "approval required"
    print(f"\nAllow {request.tool_name}?")
    print(f"  Reason: {reason}")
    print(f"  Input:  {_tool_input_preview(request.tool_input)}")
    try:
        ans = (await asyncio.to_thread(input, "  [y/N]: ")).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return ans in ("y", "yes")


def _print_help() -> None:
    print(
        "Commands:\n"
        "  /help                 Show commands\n"
        "  /settings             Show current model, mode, cwd, session\n"
        "  /clear                Start a fresh session\n"
        "  /resume <session_id>  Resume an existing session\n"
        "  /fork <session_id>    Fork an existing session into a new session\n"
        "  /exit, /quit          Exit"
    )


def _print_settings(state: "ReplState") -> None:
    from d2c.trust import get_trust_gate

    config = state.config
    try:
        trusted = get_trust_gate().is_project_trusted
    except Exception:
        trusted = "unknown"
    sid = getattr(state.session_store, "session_id", None)
    print(
        f"Settings:\n"
        f"  model:       {config.model}\n"
        f"  permission:  {config.permission_mode}\n"
        f"  cwd:         {config.cwd}\n"
        f"  session:     {sid}\n"
        f"  stream:      {'on' if state.stream else 'off'}\n"
        f"  sandbox:     {'on' if config.sandbox_enabled else 'off'}\n"
        f"  trusted:     {trusted}"
    )


async def _switch_session(state: ReplState, new_store, event_verb: str) -> None:
    """Phase 40: fire SESSION_END for the outgoing session and SESSION_START
    for the incoming one on /clear, /resume, /fork."""
    from d2c.tools import get_active_hooks

    hooks = get_active_hooks()
    old_id = getattr(state.session_store, "session_id", None)
    new_id = getattr(new_store, "session_id", None)
    if hooks is not None:
        try:
            await hooks.fire(HookEvent.SESSION_END, {"session_id": old_id, "reason": event_verb})
            await hooks.fire(
                HookEvent.SESSION_START,
                {
                    "session_id": new_id,
                    "cwd": str(state.config.cwd),
                    "model": state.config.model,
                    "mode": state.config.permission_mode,
                    "via": event_verb,
                },
            )
        except Exception:
            pass
    state.session_store = new_store
    _install_file_history(state.config, new_store)
    from d2c.observability import audit, set_context

    set_context(session_id=new_id)
    audit(
        f"session_{event_verb}" if event_verb in ("resume", "fork") else "session_start",
        session_id=new_id,
        from_session_id=old_id,
    )


async def handle_slash_command(cmd: SlashCommand, state: ReplState) -> bool:
    """Dispatch a REPL slash command. Returns True to keep the REPL running,
    False to exit. Mutates `state` (session_store / conversation) in place.

    Unknown commands are reported locally and never sent to the model.
    """
    name = cmd.name

    if name in ("/exit", "/quit"):
        return False

    if name == "/help":
        _print_help()
        return True

    if name == "/settings":
        _print_settings(state)
        return True

    if name == "/clear":
        new_store = SessionManager().create_session(state.config.cwd)
        await _switch_session(state, new_store, "clear")
        state.conversation.clear()
        print(f"Cleared. New session: {state.session_store.session_id}")
        return True

    if name == "/resume":
        if not cmd.args:
            print("Usage: /resume <session_id>")
            return True
        try:
            store, restored = SessionManager().resume_session(cmd.args[0], state.config.cwd)
        except Exception as e:
            print(f"Could not resume: {e}")
            return True
        await _switch_session(state, store, "resume")
        state.conversation[:] = list(restored)
        print(f"Resumed session {store.session_id} ({len(state.conversation)} messages).")
        return True

    if name == "/fork":
        if not cmd.args:
            print("Usage: /fork <session_id>")
            return True
        try:
            store = SessionManager().fork_session(cmd.args[0], state.config.cwd)
            _, restored = SessionManager().resume_session(cmd.args[0], state.config.cwd)
        except Exception as e:
            print(f"Could not fork: {e}")
            return True
        await _switch_session(state, store, "fork")
        state.conversation[:] = list(restored)
        print(f"Forked {cmd.args[0]} → new session {store.session_id}.")
        return True

    print(f"Unknown command: {name}")
    return True


async def run_headless(prompt: str, args: argparse.Namespace) -> None:
    """Single-shot headless execution: claude -p equivalent."""
    config = Config.load(args.cwd)

    # Override from CLI args
    if args.model:
        config.model = args.model
    config.max_turns = args.max_turns

    # Validate config (Phase 10)
    for warning in config.validate():
        print(f"Warning: {warning}", file=sys.stderr)

    # Phase 32: Force restricted permission mode in untrusted workspaces
    from d2c.trust import get_trust_gate

    if not get_trust_gate().is_project_trusted:
        if config.permission_mode not in ("default", "plan"):
            print(
                f"Warning: Untrusted workspace — overriding permission mode "
                f"'{config.permission_mode}' to 'default'.",
                file=sys.stderr,
            )
            config.permission_mode = "default"

    # Setup session
    session_store, resume_messages = _setup_session(args, config)

    # Phase 13: Load plugins — register hooks, collect skills/agents
    hook_registry = HookRegistry.from_config(config)
    plugin_skills, plugin_agents = _load_plugins(config, hook_registry)

    # Phase 34: wire runtime subsystems + fire SessionStart
    await _wire_runtime(config, session_store, hook_registry)

    # Assemble tools
    pool_config = _pool_config_from(config)
    tools = await assembleToolPool(pool_config)

    # Build loop config
    compact_config = CompactConfig(
        tool_result_max_chars=config.tool_result_max_chars,
        pressure_threshold=config.pressure_threshold,
        context_window_tokens=config.context_window_tokens,
    )
    loop_config = LoopConfig(
        system_prompt=getSystemPrompt(),
        user_context=getUserContext(config),
        model=config.model,
        max_turns=config.max_turns,
        tools=tools,
        permission_engine=PermissionEngine.from_config(config),
        hooks=hook_registry,
        config=config,
        deepseek_api_key=config.deepseek_api_key,
        deepseek_base_url=config.deepseek_base_url,
        session_store=session_store,
        compact_config=compact_config,
        stream=True,  # Phase 10: streaming enabled
    )

    # Assemble context
    system_context = getSystemContext(config)
    history = resume_messages if resume_messages else []
    history.append({"role": "user", "content": prompt})
    full_prompt, messages = assembleMessages(
        loop_config.system_prompt,
        system_context,
        loop_config.user_context,
        history,
    )
    loop_config.system_prompt = full_prompt

    # Phase 22: Record prompt in global history
    prompt_history = PromptHistory()
    prompt_history.append(prompt)

    # Phase 15: Fire Setup hook after initialization
    await hook_registry.fire(
        HookEvent.SETUP,
        {
            "session_id": session_store.session_id if session_store else None,
            "model": config.model,
        },
    )

    # Phase 34: Fire UserPromptSubmit; honor block / injected context.
    ups = await hook_registry.fire(
        HookEvent.USER_PROMPT_SUBMIT,
        {
            "prompt": prompt,
            "session_id": session_store.session_id if session_store else None,
        },
    )
    if getattr(ups, "decision", None) == "deny" or getattr(ups, "veto", False):
        print("[prompt blocked by UserPromptSubmit hook]")
        await hook_registry.fire(
            HookEvent.SESSION_END,
            {
                "session_id": session_store.session_id if session_store else None,
            },
        )
        return
    if getattr(ups, "additional_context", None):
        messages.insert(0, {"role": "user", "content": ups.additional_context})

    # Run loop
    try:
        async for event in queryLoop(loop_config, messages):
            if isinstance(event, TextDelta):
                print(event.text, end="", flush=True)
            elif isinstance(event, LoopTextResponse):
                print(event.text)
            elif isinstance(event, ToolExecutionEvent):
                print(f"\n  [{event.tool_use.name}] {event.result.output[:300]}", end="")
                if len(event.result.output) > 300:
                    print("...")
                else:
                    print()
            elif isinstance(event, StopEvent):
                if event.reason != "model_finished":
                    print(f"\n[stopped: {event.reason}]")
    finally:
        # Phase 15: Fire SessionEnd hook
        audit("session_end", session_id=(session_store.session_id if session_store else None))
        await hook_registry.fire(
            HookEvent.SESSION_END,
            {
                "session_id": session_store.session_id if session_store else None,
            },
        )


async def run_interactive(args: argparse.Namespace) -> None:
    """Interactive REPL with prompt_toolkit rich console (Phase 31).

    Features: syntax-highlighted input, slash-command auto-completion,
    file path suggestions, tool name completion, fuzzy history search,
    and a formatted bottom status bar.
    """
    config = Config.load(args.cwd)
    config.model = args.model or config.model
    config.max_turns = args.max_turns

    # Validate config (Phase 10)
    for warning in config.validate():
        print(f"Warning: {warning}", file=sys.stderr)

    # Phase 32: Force restricted permission mode in untrusted workspaces
    from d2c.trust import get_trust_gate

    if not get_trust_gate().is_project_trusted:
        if config.permission_mode not in ("default", "plan"):
            print(
                f"Warning: Untrusted workspace — overriding permission mode "
                f"'{config.permission_mode}' to 'default'.",
                file=sys.stderr,
            )
            config.permission_mode = "default"

    # Setup session
    session_store, _ = _setup_session(args, config)

    # Phase 13: Load plugins once at startup
    hook_registry = HookRegistry.from_config(config)
    plugin_skills, plugin_agents = _load_plugins(config, hook_registry)

    # Phase 34: wire runtime subsystems + fire SessionStart
    await _wire_runtime(config, session_store, hook_registry)

    compact_config = CompactConfig(
        tool_result_max_chars=config.tool_result_max_chars,
        pressure_threshold=config.pressure_threshold,
        context_window_tokens=config.context_window_tokens,
    )

    pool_config = _pool_config_from(config)
    tools = await assembleToolPool(pool_config)

    system_context = getSystemContext(config)
    system_prompt = getSystemPrompt()

    print(f"d2c ({config.model})")
    print(f"Session: {session_store.session_id if session_store else 'none'}")
    print("Type /exit or press Ctrl+D to quit. Ctrl+C to clear line.")
    print()

    # Phase 15: Fire Setup hook after initialization
    await hook_registry.fire(
        HookEvent.SETUP,
        {
            "session_id": session_store.session_id if session_store else None,
            "model": config.model,
        },
    )

    # Phase 31: Setup prompt_toolkit PromptSession with history and completions
    history_file = Path.home() / ".d2c" / "repl_history.txt"
    history_file.parent.mkdir(parents=True, exist_ok=True)

    tool_names = [t.name for t in tools]
    completer = D2CCompleter(config.cwd, tool_names)

    session = PromptSession(
        history=FileHistory(str(history_file)),
        completer=completer,
        complete_while_typing=True,
    )

    active_tasks = 0
    # Phase 36: explicit mutable REPL state that slash commands operate on.
    state = ReplState(config=config, session_store=session_store, conversation=[])

    try:
        while True:
            try:
                prompt_text = await session.prompt_async(
                    "> ",
                    bottom_toolbar=lambda: get_statusbar_text(
                        state.config,
                        state.session_store,
                        active_tasks,
                    ),
                )
                prompt_text = prompt_text.strip()
            except KeyboardInterrupt:
                print()  # Newline after ^C
                continue  # Clear current line, keep REPL alive
            except EOFError:
                print()  # Newline after ^D
                break

            if not prompt_text:
                continue

            # Bare stop words (no slash prefix).
            if prompt_text.lower() in ("exit", "quit", "q"):
                break

            # Phase 36: slash commands are handled locally and never sent to
            # the model (including unknown ones).
            cmd = parse_slash_command(prompt_text)
            if cmd is not None:
                if not await handle_slash_command(cmd, state):
                    break
                continue

            # Phase 22: Record prompt in global history
            PromptHistory().append(prompt_text)

            # Phase 34: UserPromptSubmit hook — honor block / injected context
            ups = await hook_registry.fire(
                HookEvent.USER_PROMPT_SUBMIT,
                {
                    "prompt": prompt_text,
                    "session_id": getattr(state.session_store, "session_id", None),
                },
            )
            if getattr(ups, "decision", None) == "deny" or getattr(ups, "veto", False):
                print("[prompt blocked by UserPromptSubmit hook]")
                continue

            loop_config = LoopConfig(
                system_prompt=system_prompt,
                user_context=getUserContext(config),
                model=config.model,
                max_turns=config.max_turns,
                tools=tools,
                permission_engine=PermissionEngine.from_config(config),
                hooks=hook_registry,
                config=config,
                deepseek_api_key=config.deepseek_api_key,
                deepseek_base_url=config.deepseek_base_url,
                session_store=state.session_store,
                compact_config=compact_config,
                stream=state.stream,  # Phase 10: streaming enabled
                approval_callback=interactive_approval,  # Phase 43: interactive ASK
            )

            # Phase 34: multi-turn — carry running conversation into the loop
            if getattr(ups, "additional_context", None):
                state.conversation.append({"role": "user", "content": ups.additional_context})
            state.conversation.append({"role": "user", "content": prompt_text})

            full_prompt, messages = assembleMessages(
                loop_config.system_prompt,
                system_context,
                loop_config.user_context,
                list(state.conversation),
            )
            loop_config.system_prompt = full_prompt
            # Record user message
            if state.session_store:
                state.session_store.append(
                    SessionEntry(
                        role="user",
                        content=prompt_text,
                        timestamp=_utc_now(),
                        entry_type="message",
                    )
                )

            assistant_text = ""
            try:
                async for event in queryLoop(loop_config, messages):
                    if isinstance(event, TextDelta):
                        print(event.text, end="", flush=True)
                        assistant_text += event.text
                    elif isinstance(event, LoopTextResponse):
                        print(f"\n{event.text}\n")
                        assistant_text = event.text
                    elif isinstance(event, ToolExecutionEvent):
                        print(f"  [{event.tool_use.name}] {event.result.output[:200]}", end="")
                        if len(event.result.output) > 200:
                            print("...")
                        else:
                            print()
                    elif isinstance(event, StopEvent):
                        if event.reason not in ("model_finished",):
                            print(f"  [stopped: {event.reason}]")
            except Exception as e:
                print(f"Error: {e}")

            # Phase 34: append assistant turn so the next prompt has context
            if assistant_text:
                state.conversation.append({"role": "assistant", "content": assistant_text})
    finally:
        # Phase 15: Fire SessionEnd hook
        audit("session_end", session_id=getattr(state.session_store, "session_id", None))
        await hook_registry.fire(
            HookEvent.SESSION_END,
            {
                "session_id": getattr(state.session_store, "session_id", None),
            },
        )


def _has_local_extensions(cwd: Path) -> bool:
    """Check whether the workspace contains project-local extensions.

    Returns True if any of .d2c/plugins, .d2c/agents, .d2c/skills,
    .d2c/config.yaml, or .d2c/mcp.json exist in the workspace.
    """
    return (
        (cwd / ".d2c" / "plugins").is_dir()
        or (cwd / ".d2c" / "agents").is_dir()
        or (cwd / ".d2c" / "skills").is_dir()
        or (cwd / ".d2c" / "config.yaml").exists()
        or (cwd / ".d2c" / "mcp.json").exists()
    )


def _resolve_trust(args: argparse.Namespace) -> "WorkSpaceTrustGate":
    """Determine trust decision and initialize the global trust gate.

    Must run BEFORE Config.load() because Config.load reads .env files.

    Resolution order:
      1. --trust flag        → trust + persist
      2. --no-trust flag     → deny
      3. TrustStore lookup   → trust (previously trusted)
      4. Headless mode       → warn to stderr + deny
      5. Interactive mode    → prompt user
    """
    from d2c.trust import TrustStore, WorkSpaceTrustGate, set_trust_gate

    cwd = (args.cwd or Path.cwd()).resolve()
    store = TrustStore()
    gate = WorkSpaceTrustGate(cwd, store)

    if args.trust:
        gate.decide(True)
        store.trust(cwd)
    elif args.no_trust:
        gate.decide(False)
        if args.prompt and _has_local_extensions(cwd):
            print(
                "Error: --no-trust cannot be used with a workspace that has "
                "local extensions (.d2c/plugins, .d2c/skills, etc.). "
                "Remove the .d2c directory or use --trust instead.",
                file=sys.stderr,
            )
            sys.exit(1)
    elif store.is_trusted(cwd):
        gate.decide(True)
    elif args.prompt:
        # Headless: untrusted workspace
        gate.decide(False)
        if _has_local_extensions(cwd):
            print(
                "Error: Untrusted workspace contains local extensions "
                "(.d2c/plugins, .d2c/skills, .d2c/mcp.json, .d2c/config.yaml). "
                "Use --trust to run in this workspace, or remove the .d2c directory.",
                file=sys.stderr,
            )
            sys.exit(1)
        else:
            print(
                "Warning: untrusted workspace. Project-local features disabled "
                "(.env, plugins, skills, MCP, CLAUDE.md). Use --trust to enable.",
                file=sys.stderr,
            )
    else:
        # Interactive: prompt the user
        print(f"Workspace: {cwd}")
        print(
            "Project-local files (.env, plugins, skills, MCP, CLAUDE.md) "
            "will be loaded. Only trust workspaces you control."
        )
        trusted = gate.prompt_trust()
        gate.decide(trusted)
        if trusted:
            store.trust(cwd)

    set_trust_gate(gate)
    return gate


def _run_doctor_cli(args: argparse.Namespace) -> int:
    """Run diagnostics non-interactively and print results. Returns exit code."""
    from d2c.doctor import exit_code, render_json, render_text, run_doctor
    from d2c.trust import TrustStore, WorkSpaceTrustGate, set_trust_gate

    cwd = (args.cwd or Path.cwd()).resolve()
    if args.no_trust:
        trusted = False
    elif args.trust:
        trusted = True
    else:
        try:
            trusted = TrustStore().is_trusted(cwd)
        except Exception:
            trusted = False
    gate = WorkSpaceTrustGate(cwd)
    gate.decide(trusted)
    set_trust_gate(gate)

    config = Config.load(cwd)
    results = run_doctor(config, cwd=cwd, trusted=trusted, live=args.doctor_live)
    print(render_json(results) if args.json else render_text(results))
    return exit_code(results)


def main() -> None:
    args = parse_args()

    # Phase 23: Handle --rewind-files (no project config needed)
    if args.rewind_files:
        base_dir = Path.home() / ".d2c"
        restored = SessionFileHistory.rewind_session(
            base_dir,
            args.rewind_files,
            cwd=args.cwd,
        )
        if restored:
            print(f"Restored {len(restored)} file(s) from session {args.rewind_files}:")
            for p in restored:
                print(f"  {p}")
        else:
            print(f"No checkpoints found for session {args.rewind_files}.")
        return

    if args.mcp:
        from d2c.mcp.server import run_mcp_server

        asyncio.run(run_mcp_server(args))
        return

    if args.list_models:
        from d2c.config import DEEPSEEK_MODEL_ALIASES, DEEPSEEK_MODEL_DEFAULTS

        print("Available DeepSeek models (via Anthropic-compatible API):")
        for model_id, defaults in DEEPSEEK_MODEL_DEFAULTS.items():
            aliases = [
                k for k, v in DEEPSEEK_MODEL_ALIASES.items() if v == model_id and k != model_id
            ]
            alias_str = f" (aliases: {', '.join(aliases)})" if aliases else ""
            print(f"  {model_id}{alias_str}")
            print(
                f"    context: {defaults['context_window']:,} tokens, max_tokens: {defaults['max_tokens']}"
            )
        return

    # Phase 47: diagnostics — offline, no model/API access needed.
    if args.doctor:
        sys.exit(_run_doctor_cli(args))

    # Trust gate (must run before Config.load — reads project .env)
    _resolve_trust(args)

    if args.prompt:
        asyncio.run(run_headless(args.prompt, args))
    else:
        asyncio.run(run_interactive(args))


if __name__ == "__main__":
    main()
