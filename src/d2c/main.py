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
from prompt_toolkit import PromptSession, print_formatted_text
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
    from d2c.approvals import ApprovalCache
    from d2c.persistence import SessionStore
    from d2c.trust import WorkSpaceTrustGate
    from d2c.usage import UsageTracker


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
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Start the local HTTP server (health + session endpoints); localhost-only by default",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="With --serve: bind host (default: 127.0.0.1, localhost-only)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="With --serve: bind port (default: 8765)",
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
            "/usage",
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


_SESSION_ID_SHORT_LEN = 8
_STATUSBAR_MODEL_MAX = 24
_STATUSBAR_CWD_MAX = 20
_STATUSBAR_FALLBACK_WIDTH = 80


def _truncate_field(value: str, max_len: int) -> str:
    """Truncate a single field value to max_len chars, marking cuts with '…'."""
    if len(value) <= max_len or max_len <= 0:
        return value if max_len > 0 else ""
    if max_len == 1:
        return "…"
    return value[: max_len - 1] + "…"


def _statusbar_trust_label() -> str:
    try:
        from d2c.trust import get_trust_gate

        return "trusted" if get_trust_gate().is_project_trusted else "untrusted"
    except Exception:
        return "unknown"


def get_statusbar_text(
    config: "Config",
    session_store: Any,
    active_tasks: int = 0,
    usage: Any = None,
    width: int | None = None,
) -> HTML:
    """Return status bar text formatted as HTML for the bottom toolbar.

    Phase 57: short session id, model, permission mode, cwd basename, trust
    status, active background task count, and (Phase 55) compact token/cost
    usage once there has been a model call — all width-aware: optional
    fields are dropped (widest-to-narrowest terminal) before any field is
    hard-truncated, so a narrow terminal degrades gracefully instead of
    wrapping or corrupting the line.
    """
    import html as _html

    if width is None:
        import shutil

        try:
            width = shutil.get_terminal_size(fallback=(_STATUSBAR_FALLBACK_WIDTH, 24)).columns
        except Exception:
            width = _STATUSBAR_FALLBACK_WIDTH

    mode = getattr(config, "permission_mode", "default").upper()
    sess_id = ""
    if session_store is not None:
        sess_id = getattr(session_store, "session_id", "") or ""
    sess_short = sess_id[:_SESSION_ID_SHORT_LEN]

    model = _truncate_field(str(getattr(config, "model", "")), _STATUSBAR_MODEL_MAX)
    cwd_name = ""
    cwd = getattr(config, "cwd", None)
    if cwd:
        try:
            cwd_name = _truncate_field(Path(cwd).name, _STATUSBAR_CWD_MAX)
        except (TypeError, ValueError):
            cwd_name = ""

    core = f"Session: {sess_short} | Mode: {mode} | Model: {model}"

    optional_segments = [
        f"cwd: {cwd_name}" if cwd_name else "",
        f"Trust: {_statusbar_trust_label()}",
        f"Tasks: {active_tasks}" if active_tasks > 0 else "",
    ]
    if usage is not None and getattr(usage, "calls", 0) > 0:
        from d2c.usage import usage_status_fragment

        optional_segments.append(usage_status_fragment(usage))
    optional_segments = [s for s in optional_segments if s]

    # " d2c | " prefix + trailing space account for fixed chrome width.
    budget = max(width - len(" d2c |  "), 0)

    content = core
    for seg in optional_segments:
        # A segment that doesn't fit is skipped, not fatal — a later,
        # shorter segment may still fit.
        candidate = f"{content} | {seg}"
        if len(candidate) <= budget:
            content = candidate

    if len(content) > budget:
        content = _truncate_field(content, budget)

    return HTML(
        f"<style bg='ansiblue' fg='ansiwhite'> <b>d2c</b> | {_html.escape(content)} </style>"
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
    """Build the tool-pool config, wiring sandbox settings (Phase 34) and the
    effective permission mode (Phase 56: consumed by ConfigInfoTool)."""
    return PoolConfig(
        cwd=config.cwd,
        permission_mode=config.permission_mode,
        sandbox_config=SandboxConfig(
            enabled=config.sandbox_enabled,
            backend=config.sandbox_backend,
            network_enabled=config.sandbox_allow_network,
            fallback_to_process=config.sandbox_fallback,
        ),
    )


async def _assemble_headless_loop_config(
    config: Config, session_store: SessionStore | None
) -> tuple[LoopConfig, HookRegistry, "UsageTracker"]:
    """Build a fully-wired LoopConfig for a single-shot (non-REPL) run.

    Phase 59: shared by run_headless (CLI) and d2c.sdk.D2CClient so both
    surfaces build identical loop configuration from one code path.
    """
    hook_registry = HookRegistry.from_config(config)
    _load_plugins(config, hook_registry)
    await _wire_runtime(config, session_store, hook_registry)

    from d2c.usage import UsageTracker, set_usage_tracker

    usage_tracker = UsageTracker()
    set_usage_tracker(usage_tracker)

    pool_config = _pool_config_from(config)
    tools = await assembleToolPool(pool_config)

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
        stream=True,
    )
    return loop_config, hook_registry, usage_tracker


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
    session_store: "SessionStore | None"
    conversation: list[dict] = field(default_factory=list)
    stream: bool = True
    approvals: "ApprovalCache" = field(default_factory=lambda: _new_approval_cache())
    usage: "UsageTracker" = field(default_factory=lambda: _new_usage_tracker())


def _new_approval_cache() -> "ApprovalCache":
    """Phase 64: the live REPL opts into cross-session/restart persistence
    at ~/.d2c/approvals.json; ApprovalCache() with no path (used directly
    in tests) stays in-memory-only."""
    from d2c.approvals import DEFAULT_APPROVALS_PATH, ApprovalCache

    return ApprovalCache(path=DEFAULT_APPROVALS_PATH)


def _new_usage_tracker() -> "UsageTracker":
    from d2c.usage import UsageTracker

    return UsageTracker()


def parse_slash_command(text: str) -> "SlashCommand | None":
    """Parse `/name arg...` into a SlashCommand, or None if not a slash command."""
    text = text.strip()
    if not text.startswith("/"):
        return None
    parts = text.split()
    return SlashCommand(name=parts[0].lower(), args=parts[1:])


def _tool_input_preview(tool_input: dict) -> str:
    """Compact, redacted preview of tool input for the approval prompt.

    Phase 57: routed through observability.redact() so a secret embedded in
    the tool input (e.g. a Bash command with a literal API key) is never
    printed to the terminal, matching what the audit log already redacts.
    """
    import json

    from d2c.observability import redact

    safe_input = redact(tool_input)
    try:
        s = json.dumps(safe_input, default=str)
    except Exception:
        s = str(safe_input)
    return s[:200] + ("…" if len(s) > 200 else "")


def _permission_prompt_lines(request, result) -> list[str]:
    """Phase 57: permission-dialog body — tool name, risk category, reason,
    and a redacted input preview, formatted for easy scanning. Plain text —
    used by the headless-fallback ``interactive_approval()``. The REPL's
    styled dialog is ``_render_permission_dialog()`` (Phase 65)."""
    category = getattr(getattr(request, "tool_category", None), "value", None) or "unknown"
    reason = getattr(result, "reason", "") or "approval required"
    return [
        f"\nPermission required: {request.tool_name} [{category}]",
        f"  Reason: {reason}",
        f"  Input:  {_tool_input_preview(request.tool_input)}",
    ]


async def interactive_approval(request, result) -> bool:
    """Phase 43: prompt the user to approve/deny an ASK tool request.

    Default is deny (empty input). `y`/`yes` approves once. Runs input() off
    the event loop so streaming isn't blocked. Plain-text: used in headless
    fallback paths where styled TUI output isn't appropriate.
    """
    for line in _permission_prompt_lines(request, result):
        print(line)
    try:
        ans = (await asyncio.to_thread(input, "  Allow? [y/N]: ")).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return ans in ("y", "yes")


# ── Phase 65: styled REPL permission dialog ─────────────────────────

_CATEGORY_DIALOG_COLORS: dict[str, str] = {
    "read": "ansigreen",
    "shell": "ansiyellow",
    "write": "ansired",
    "meta": "ansicyan",
}
_RISK_DIALOG_COLORS: dict[str, str] = {
    "allow": "ansigreen",
    "ask": "ansiyellow",
    "deny": "ansired",
}
# Tools whose input already contains enough to compute a real diff WITHOUT
# reading anything from disk (old_string/new_string for Edit; the unified
# diff text itself for ApplyPatch). Write has no "old" side available, so it
# gets a content preview, not a true diff.
_DIFF_CAPABLE_TOOLS = frozenset({"Edit", "Write", "ApplyPatch"})
_INLINE_DIFF_THRESHOLD = 10  # short diffs show inline even when collapsed
_MAX_DIFF_DISPLAY_LINES = 60  # cap even an expanded ("d") diff


def _bash_risk_verdict(command: str) -> str:
    """'allow' | 'ask' | 'deny' risk bucket for a Bash command, reusing the
    existing acceptEdits structural classifier as the color signal rather
    than duplicating a command-name heuristic."""
    from d2c.permissions.classifier import classify_accept_edits_shell

    try:
        return classify_accept_edits_shell(command)
    except Exception:
        return "ask"


def _diff_preview(tool_name: str, tool_input: dict) -> tuple[str, list[str]]:
    """(summary, diff_lines) computed ONLY from the tool_input already
    provided — never reads the file from disk (the permission gate must not
    read speculatively). Diff/line-count math runs on the raw content so
    truncation never corrupts it; each individual line is redacted before
    being returned for display.
    """
    import difflib

    from d2c.observability import redact

    def _r(line: str) -> str:
        return str(redact(line))

    if tool_name == "Edit":
        old = str(tool_input.get("old_string", "")).splitlines()
        new = str(tool_input.get("new_string", "")).splitlines()
        raw = list(difflib.unified_diff(old, new, lineterm=""))
        plus = sum(1 for x in raw if x.startswith("+") and not x.startswith("+++"))
        minus = sum(1 for x in raw if x.startswith("-") and not x.startswith("---"))
        return f"+{plus} / -{minus}", [_r(x) for x in raw]

    if tool_name == "Write":
        content = str(tool_input.get("content", ""))
        lines = content.splitlines()
        return f"+{len(lines)} (new content)", [_r(f"+{x}") for x in lines]

    if tool_name == "ApplyPatch":
        patch = str(tool_input.get("patch", ""))
        lines = patch.splitlines()
        plus = sum(1 for x in lines if x.startswith("+") and not x.startswith("+++"))
        minus = sum(1 for x in lines if x.startswith("-") and not x.startswith("---"))
        return f"+{plus} / -{minus}", [_r(x) for x in lines]

    return "", []


def _render_permission_dialog(request, result, *, expand_diff: bool = False) -> None:
    """Phase 65: styled color-coded permission dialog via prompt_toolkit.

    - Header: tool name + category badge, colored by PermissionCategory
      (green=READ, yellow=SHELL, red=WRITE, cyan=META).
    - Input preview, per tool type: Bash gets risk-colored command text
      (reusing the acceptEdits classifier); Edit/Write/ApplyPatch get a
      diff summary (+N / -M) and, if short (or "d" expanded), the diff
      itself with +green/-red lines; WebFetch/WebSearch show the
      URL/query; everything else falls back to a redacted JSON preview.
    - All interpolated content is HTML-escaped and redacted — never raw
      secrets, never a string that could break out of the markup.
    """
    import html as _html

    def esc(s: str) -> str:
        return _html.escape(str(s))

    tool_name = getattr(request, "tool_name", "")
    tool_input = request.tool_input if isinstance(request.tool_input, dict) else {}
    category = getattr(getattr(request, "tool_category", None), "value", None) or "unknown"
    reason = getattr(result, "reason", "") or "approval required"
    cat_color = _CATEGORY_DIALOG_COLORS.get(category, "ansicyan")

    lines = [
        f"\n<b>Permission required:</b> <b>{esc(tool_name)}</b> "
        f"<{cat_color}>[{esc(category)}]</{cat_color}>",
        f"  Reason: {esc(reason)}",
    ]

    diff_summary = ""
    diff_lines: list[str] = []
    if tool_name == "Bash":
        command = str(tool_input.get("command", ""))
        verdict = _bash_risk_verdict(command)
        color = _RISK_DIALOG_COLORS.get(verdict, "ansiyellow")
        from d2c.observability import redact

        safe_command = str(redact(command))
        lines.append(f"  Command: <{color}>{esc(safe_command)}</{color}>")
    elif tool_name == "WebFetch":
        from d2c.observability import redact

        url = str(redact(tool_input.get("url", "")))
        lines.append(f"  URL: {esc(url)}")
    elif tool_name == "WebSearch":
        from d2c.observability import redact

        query = str(redact(tool_input.get("query", "")))
        lines.append(f"  Query: {esc(query)}")
    elif tool_name in _DIFF_CAPABLE_TOOLS:
        diff_summary, diff_lines = _diff_preview(tool_name, tool_input)
        file_path = str(tool_input.get("file_path", ""))
        lines.append(f"  {esc(file_path)}  ({esc(diff_summary)})")
        if diff_lines and (expand_diff or len(diff_lines) < _INLINE_DIFF_THRESHOLD):
            shown = diff_lines[:_MAX_DIFF_DISPLAY_LINES]
            for d_line in shown:
                if d_line.startswith("+") and not d_line.startswith("+++"):
                    lines.append(f"    <ansigreen>{esc(d_line)}</ansigreen>")
                elif d_line.startswith("-") and not d_line.startswith("---"):
                    lines.append(f"    <ansired>{esc(d_line)}</ansired>")
                else:
                    lines.append(f"    {esc(d_line)}")
            if len(diff_lines) > len(shown):
                lines.append(f"    ... [{len(diff_lines) - len(shown)} more lines]")
    else:
        lines.append(f"  Input:  {esc(_tool_input_preview(tool_input))}")

    choices = "[y] once  [a] session  [A] always  [n] deny"
    if tool_name in _DIFF_CAPABLE_TOOLS and diff_lines and not expand_diff:
        choices += "  [d] diff"
    lines.append(f"  {choices}")

    # file=sys.stdout (looked up fresh here, not cached) avoids a
    # prompt_toolkit issue where its DEFAULT output object caches a stdout
    # reference across calls — under pytest's capsys (which swaps sys.stdout
    # per test), that stale reference raises "I/O operation on closed file"
    # on the second call. Passing file= explicitly sidesteps the cache and
    # is equally correct for a real terminal.
    print_formatted_text(HTML("\n".join(lines)), file=sys.stdout)


def make_interactive_approval(cache: "ApprovalCache"):
    """Phase 52/64/65: build an approval callback backed by a session-scoped,
    optionally-persistent cache, rendering a styled permission dialog.

    Prompt options:
      [y] allow once (not cached)
      [a] session — cached in memory only; forgotten on clear()/restart even
          if the cache is disk-backed
      [A] always — cached AND persisted to disk (Phase 64), survives
          clear()/restart
      [d] diff — re-render with the full diff expanded, then re-prompt
      anything else / empty — deny (default)

    A cache hit (either scope) skips the full dialog and prints one styled
    confirmation line, emitting ``permission_approved_cached``.

    Phase 59 fix: concurrent-safe tools (e.g. multiple reads in one turn)
    can all need approval at once. Without serialization, their prompts and
    ``input()`` calls interleave on the same stdin — confusing at best, and
    at worst an answer lands on the wrong prompt. A lock scoped to this
    callback instance (one per REPL session) makes prompts resolve one at a
    time; the cache is rechecked after acquiring it, since a concurrent
    "always" approval for the identical action may have landed while
    waiting.
    """
    prompt_lock = asyncio.Lock()

    def _cache_hit_line(request) -> None:
        import html as _html

        tool_name = _html.escape(str(getattr(request, "tool_name", "")))
        print_formatted_text(
            HTML(f"<ansigreen>✓</ansigreen> {tool_name} — approved (cached)"),
            file=sys.stdout,
        )

    async def _cb(request, result) -> bool:
        if cache.is_approved(request):
            _cache_hit_line(request)
            audit(
                "permission_approved_cached",
                tool_name=request.tool_name,
                category=getattr(request.tool_category, "value", None),
            )
            return True
        async with prompt_lock:
            if cache.is_approved(request):
                _cache_hit_line(request)
                audit(
                    "permission_approved_cached",
                    tool_name=request.tool_name,
                    category=getattr(request.tool_category, "value", None),
                )
                return True

            expand_diff = False
            while True:
                _render_permission_dialog(request, result, expand_diff=expand_diff)
                try:
                    raw = (await asyncio.to_thread(input, "  Choice: ")).strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    return False
                low = raw.lower()
                if low in ("d", "diff"):
                    expand_diff = True
                    continue
                if low in ("y", "yes"):
                    return True
                if raw == "a" or low == "session":
                    cache.approve(request, persist=False)
                    return True
                if raw == "A" or low == "always":
                    cache.approve(request)
                    return True
                return False

    return _cb


def _print_help() -> None:
    print(
        "Commands:\n"
        "  /help                 Show commands\n"
        "  /settings             Show current model, mode, cwd, session\n"
        "  /usage                Show session token usage and estimated cost\n"
        "  /clear                Start a fresh session\n"
        "  /resume <session_id>  Resume an existing session\n"
        "  /fork <session_id>    Fork an existing session into a new session\n"
        "  /exit, /quit          Exit"
    )


def _print_settings(state: "ReplState") -> None:
    from d2c.trust import get_trust_gate

    config = state.config
    trusted: bool | str
    try:
        trusted = get_trust_gate().is_project_trusted
    except Exception:
        trusted = "unknown"
    sid = getattr(state.session_store, "session_id", None)

    # Phase 57: background task visibility + readable session usage state.
    try:
        from d2c.subagent import get_background_manager

        bg_active = get_background_manager().active_count
    except Exception:
        bg_active = 0

    usage_line = "  usage:       (no model calls yet)"
    session_usage = getattr(getattr(state, "usage", None), "session", None)
    if session_usage is not None and getattr(session_usage, "calls", 0) > 0:
        from d2c.usage import usage_status_fragment

        usage_line = (
            f"  usage:       {session_usage.calls} call(s), {usage_status_fragment(session_usage)}"
        )

    print(
        f"Settings:\n"
        f"  model:       {config.model}\n"
        f"  permission:  {config.permission_mode}\n"
        f"  cwd:         {config.cwd}\n"
        f"  session:     {sid}\n"
        f"  stream:      {'on' if state.stream else 'off'}\n"
        f"  sandbox:     {'on' if config.sandbox_enabled else 'off'}\n"
        f"  trusted:     {trusted}\n"
        f"  bg tasks:    {bg_active}\n"
        f"{usage_line}"
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
    # Phase 52: session-scoped approvals never survive a session switch.
    state.approvals.clear()
    # Phase 55: flush usage totals for the outgoing session, then reset.
    from d2c.usage import audit_session_usage

    audit_session_usage(state.usage.session, session_id=old_id)
    state.usage.reset()
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

    if name == "/usage":
        from d2c.usage import format_session_usage

        sid = getattr(state.session_store, "session_id", None)
        print(format_session_usage(state.usage.session, session_id=sid))
        return True

    if name == "/clear":
        new_store = SessionManager().create_session(state.config.cwd)
        await _switch_session(state, new_store, "clear")
        state.conversation.clear()
        print(f"Cleared. New session: {new_store.session_id}")
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

    # Phase 59: shared assembly (plugins, runtime wiring, usage tracker, tool
    # pool, LoopConfig) — identical to what d2c.sdk.D2CClient builds.
    loop_config, hook_registry, usage_tracker = await _assemble_headless_loop_config(
        config, session_store
    )
    from d2c.usage import audit_session_usage

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
        # Phase 55: flush session usage totals, then Phase 15 SessionEnd hook
        audit_session_usage(
            usage_tracker.session,
            session_id=(session_store.session_id if session_store else None),
        )
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

    session: PromptSession[str] = PromptSession(
        history=FileHistory(str(history_file)),
        completer=completer,
        complete_while_typing=True,
    )

    # Phase 36: explicit mutable REPL state that slash commands operate on.
    state = ReplState(config=config, session_store=session_store, conversation=[])

    # Phase 55: session usage accounting (read by /usage and the status bar).
    from d2c.usage import audit_session_usage, set_usage_tracker

    set_usage_tracker(state.usage)
    # Phase 52: approval callback backed by the session-scoped cache (cleared in
    # place on /clear/resume/fork, so this closure stays valid).
    _approval_cb = make_interactive_approval(state.approvals)

    def _active_bg_tasks() -> int:
        # Phase 57: live background-subagent count for the status bar.
        try:
            from d2c.subagent import get_background_manager

            return get_background_manager().active_count
        except Exception:
            return 0

    try:
        while True:
            try:
                prompt_text = await session.prompt_async(
                    "> ",
                    bottom_toolbar=lambda: get_statusbar_text(
                        state.config,
                        state.session_store,
                        _active_bg_tasks(),
                        usage=state.usage.session,
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
                approval_callback=_approval_cb,  # Phase 43/52: interactive ASK + session cache
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
        # Phase 55: flush session usage totals, then Phase 15 SessionEnd hook
        audit_session_usage(
            state.usage.session, session_id=getattr(state.session_store, "session_id", None)
        )
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


def _build_eval_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="d2c eval", description="Run a headless eval corpus (Phase 66)"
    )
    parser.add_argument("corpus", type=Path, help="Path to a YAML eval corpus")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("./eval-results"),
        help="Directory for per-task and summary JSON output (default: ./eval-results)",
    )
    parser.add_argument("--model", default=None, help="DeepSeek model to use for eval tasks")
    parser.add_argument(
        "--permission-mode",
        default="bypass",
        help="Permission mode for eval tasks (default: bypass — deny rules still apply)",
    )
    parser.add_argument("--max-turns", type=int, default=25, help="Maximum agent turns per task")
    trust_group = parser.add_mutually_exclusive_group()
    trust_group.add_argument(
        "--trust", action="store_true", help="Trust each task's repo (required for edits/bash)"
    )
    trust_group.add_argument(
        "--no-trust",
        action="store_true",
        help="Do not trust task repos, even if previously trusted",
    )
    return parser


def _run_eval_cli(argv: list[str]) -> int:
    """`d2c eval <corpus.yaml> --out-dir <dir>` — headless eval harness (Phase 66)."""
    from d2c.eval import EvalCorpus, run_eval

    args = _build_eval_parser().parse_args(argv)

    try:
        corpus = EvalCorpus.load(args.corpus)
    except Exception as exc:
        print(f"Error loading corpus {args.corpus}: {exc}", file=sys.stderr)
        return 1

    if not corpus.tasks:
        print(f"Corpus {args.corpus} has no tasks.", file=sys.stderr)
        return 1

    trust = True if args.trust else False if args.no_trust else None
    print(f"Running {len(corpus.tasks)} eval task(s) -> {args.out_dir}")
    summary = asyncio.run(
        run_eval(
            corpus,
            args.out_dir,
            model=args.model,
            permission_mode=args.permission_mode,
            max_turns=args.max_turns,
            trust=trust,
        )
    )
    print(
        f"Done: {summary.success_count}/{summary.task_count} succeeded, "
        f"mean {summary.mean_turns} turns, ${summary.total_cost_estimate:.4f} estimated cost"
    )
    return 0


def main() -> None:
    # Phase 66: `d2c eval <corpus.yaml>` is a separate mini-CLI, dispatched
    # before parse_args() so it never touches the flat prompt/flag parser.
    if len(sys.argv) > 1 and sys.argv[1] == "eval":
        sys.exit(_run_eval_cli(sys.argv[2:]))

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

    if args.serve:
        from d2c.server import D2CServer

        _resolve_trust(args)
        server = D2CServer(host=args.host, port=args.port, cwd=(args.cwd or Path.cwd()).resolve())
        print(f"d2c server listening on http://{args.host}:{args.port}")
        asyncio.run(server.serve_forever())
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
