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
        hooks=HookRegistry.from_config(config),
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

    # Run loop
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

        loop_config = LoopConfig(
            system_prompt=system_prompt,
            user_context=getUserContext(config),
            model=config.model,
            max_turns=config.max_turns,
            tools=tools,
            permission_engine=PermissionEngine.from_config(config),
            hooks=HookRegistry.from_config(config),
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


def main() -> None:
    args = parse_args()

    if args.list_models:
        from d2c.config import DEEPSEEK_MODEL_DEFAULTS, DEEPSEEK_MODEL_ALIASES
        print("Available DeepSeek models (via Anthropic-compatible API):")
        for model_id, defaults in DEEPSEEK_MODEL_DEFAULTS.items():
            aliases = [k for k, v in DEEPSEEK_MODEL_ALIASES.items() if v == model_id and k != model_id]
            alias_str = f" (aliases: {', '.join(aliases)})" if aliases else ""
            print(f"  {model_id}{alias_str}")
            print(f"    context: {defaults['context_window']:,} tokens, max_tokens: {defaults['max_tokens']}")
        return

    if args.prompt:
        asyncio.run(run_headless(args.prompt, args))
    else:
        asyncio.run(run_interactive(args))


if __name__ == "__main__":
    main()
