"""Entry point — CLI dispatch.

Paper: All entry surfaces converge on the same agent loop.
Interactive CLI and headless (claude -p equivalent) both feed queryLoop().
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from d2c.config import Config
from d2c.context import (
    SystemContext,
    assembleMessages,
    getSystemContext,
    getSystemPrompt,
    getUserContext,
)
from d2c.loop import LoopConfig, queryLoop
from d2c.loop import TextDelta, TextResponse as LoopTextResponse
from d2c.loop import ToolExecutionEvent, StopEvent
from d2c.compact import CompactConfig
from d2c.hooks import HookRegistry
from d2c.permissions import PermissionEngine
from d2c.persistence import SessionEntry, SessionManager, SessionStore, _utc_now
from d2c.tools.pool import Config as PoolConfig
from d2c.tools.pool import assembleToolPool
from d2c.plugins.loader import PluginLoader
from d2c.hooks import HookDefinition, HookEvent, HookType
from d2c.file_history import FileHistory
from d2c.history import PromptHistory


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="d2c — an interactive CLI coding agent")
    parser.add_argument("prompt", nargs="?", help="Single-shot prompt (omit for interactive REPL)")
    parser.add_argument("--model", default=None, help="DeepSeek model to use (v4-pro, chat/v3, reasoner/r1)")
    parser.add_argument("--max-turns", type=int, default=25, help="Maximum agent turns")
    parser.add_argument("--cwd", type=Path, default=None, help="Working directory")
    parser.add_argument("--session", default=None, help="Session ID to use")
    parser.add_argument("--resume", default=None, help="Session ID to resume")
    parser.add_argument("--fork", default=None, help="Session ID to fork from")
    parser.add_argument("--list-models", action="store_true", help="List available DeepSeek models and exit")
    parser.add_argument("--rewind-files", default=None, metavar="SESSION_ID",
                        help="Revert all filesystem changes from the given session")

    # Trust gate flags (mutually exclusive)
    trust_group = parser.add_mutually_exclusive_group()
    trust_group.add_argument(
        "--trust", action="store_true",
        help="Trust the workspace and load all project-local features",
    )
    trust_group.add_argument(
        "--no-trust", action="store_true",
        help="Run without trusting the workspace (skip project-local .env, plugins, skills, MCP, memory)",
    )

    return parser.parse_args()


def _setup_session(args: argparse.Namespace, config: Config) -> tuple[SessionStore | None, list[dict] | None]:
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
        store.append(SessionEntry(
            role="system", content="",
            timestamp=_utc_now(),
            entry_type="message",
            metadata={"event": "session_start", "cwd": str(cwd)},
        ))
        print(f"Session: {store.session_id}")
        return store, None
    else:
        store = manager.create_session(cwd)
        print(f"Session: {store.session_id}")
        return store, None


def _load_plugins(
    config: Config, hook_registry: HookRegistry,
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
                    f"Warning: Plugin '{plugin.manifest.name}': "
                    f"invalid hook definition: {e}",
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
                    plugin_agents.append({
                        "name": agent_path.stem,
                        "path": str(agent_path),
                        "source": f"plugin:{plugin.manifest.name}",
                    })
                    plugin.agents_loaded += 1

    return plugin_skills, plugin_agents


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

    # Setup session
    session_store, resume_messages = _setup_session(args, config)

    # Phase 13: Load plugins — register hooks, collect skills/agents
    hook_registry = HookRegistry.from_config(config)
    plugin_skills, plugin_agents = _load_plugins(config, hook_registry)

    # Assemble tools
    pool_config = PoolConfig(cwd=config.cwd)
    tools = await assembleToolPool(pool_config)

    # Build loop config (with stubs for not-yet-implemented phases)
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
    await hook_registry.fire(HookEvent.SETUP, {
        "session_id": session_store.session_id if session_store else None,
        "model": config.model,
    })

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
        await hook_registry.fire(HookEvent.SESSION_END, {
            "session_id": session_store.session_id if session_store else None,
        })


async def run_interactive(args: argparse.Namespace) -> None:
    """Interactive REPL."""
    config = Config.load(args.cwd)
    config.model = args.model or config.model
    config.max_turns = args.max_turns

    # Validate config (Phase 10)
    for warning in config.validate():
        print(f"Warning: {warning}", file=sys.stderr)

    # Setup session
    session_store, _ = _setup_session(args, config)

    # Phase 13: Load plugins once at startup
    hook_registry = HookRegistry.from_config(config)
    plugin_skills, plugin_agents = _load_plugins(config, hook_registry)

    compact_config = CompactConfig(
        tool_result_max_chars=config.tool_result_max_chars,
        pressure_threshold=config.pressure_threshold,
        context_window_tokens=config.context_window_tokens,
    )

    pool_config = PoolConfig(cwd=config.cwd)
    tools = await assembleToolPool(pool_config)

    system_context = getSystemContext(config)
    system_prompt = getSystemPrompt()

    print(f"d2c ({config.model})")
    print(f"Session: {session_store.session_id if session_store else 'none'}")
    print("Type 'exit' or press Ctrl+C to quit.")
    print()

    # Phase 15: Fire Setup hook after initialization
    await hook_registry.fire(HookEvent.SETUP, {
        "session_id": session_store.session_id if session_store else None,
        "model": config.model,
    })

    try:
        while True:
            try:
                prompt_text = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not prompt_text:
                continue
            if prompt_text.lower() in ("exit", "quit", "q"):
                break

            # Phase 22: Record prompt in global history
            prompt_history = PromptHistory()
            prompt_history.append(prompt_text)

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
                session_store=session_store,
                compact_config=compact_config,
                stream=True,  # Phase 10: streaming enabled
            )

            full_prompt, messages = assembleMessages(
                loop_config.system_prompt,
                system_context,
                loop_config.user_context,
                [{"role": "user", "content": prompt_text}],
            )
            loop_config.system_prompt = full_prompt
            # Record user message
            if session_store:
                session_store.append(SessionEntry(
                    role="user", content=prompt_text,
                    timestamp=_utc_now(), entry_type="message",
                ))

            try:
                async for event in queryLoop(loop_config, messages):
                    if isinstance(event, TextDelta):
                        print(event.text, end="", flush=True)
                    elif isinstance(event, LoopTextResponse):
                        print(f"\n{event.text}\n")
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
    finally:
        # Phase 15: Fire SessionEnd hook
        await hook_registry.fire(HookEvent.SESSION_END, {
            "session_id": session_store.session_id if session_store else None,
        })


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
    elif store.is_trusted(cwd):
        gate.decide(True)
    elif args.prompt:
        # Headless: untrusted workspace
        gate.decide(False)
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


def main() -> None:
    args = parse_args()

    # Phase 23: Handle --rewind-files (no project config needed)
    if args.rewind_files:
        base_dir = Path.home() / ".d2c"
        restored = FileHistory.rewind_session(
            base_dir, args.rewind_files, cwd=args.cwd,
        )
        if restored:
            print(f"Restored {len(restored)} file(s) from session {args.rewind_files}:")
            for p in restored:
                print(f"  {p}")
        else:
            print(f"No checkpoints found for session {args.rewind_files}.")
        return

    if args.list_models:
        from d2c.config import DEEPSEEK_MODEL_DEFAULTS, DEEPSEEK_MODEL_ALIASES
        print("Available DeepSeek models (via Anthropic-compatible API):")
        for model_id, defaults in DEEPSEEK_MODEL_DEFAULTS.items():
            aliases = [k for k, v in DEEPSEEK_MODEL_ALIASES.items() if v == model_id and k != model_id]
            alias_str = f" (aliases: {', '.join(aliases)})" if aliases else ""
            print(f"  {model_id}{alias_str}")
            print(f"    context: {defaults['context_window']:,} tokens, max_tokens: {defaults['max_tokens']}")
        return

    # Trust gate (must run before Config.load — reads project .env)
    _resolve_trust(args)

    if args.prompt:
        asyncio.run(run_headless(args.prompt, args))
    else:
        asyncio.run(run_interactive(args))


if __name__ == "__main__":
    main()
