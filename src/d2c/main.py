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
from d2c.loop import LoopConfig, StubHookRegistry, StubPermissionEngine, queryLoop
from d2c.loop import TextResponse as LoopTextResponse
from d2c.loop import ToolExecutionEvent, StopEvent
from d2c.tools.pool import Config as PoolConfig
from d2c.tools.pool import assembleToolPool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="d2c — an interactive CLI coding agent")
    parser.add_argument("prompt", nargs="?", help="Single-shot prompt (omit for interactive REPL)")
    parser.add_argument("--model", default="deepseek-v4-pro", help="Model to use")
    parser.add_argument("--max-turns", type=int, default=25, help="Maximum agent turns")
    parser.add_argument("--cwd", type=Path, default=None, help="Working directory")
    return parser.parse_args()


async def run_headless(prompt: str, args: argparse.Namespace) -> None:
    """Single-shot headless execution: claude -p equivalent."""
    config = Config.load(args.cwd)

    # Override from CLI args
    if args.model:
        config.model = args.model
    config.max_turns = args.max_turns

    # Assemble tools
    pool_config = PoolConfig(cwd=config.cwd)
    tools = await assembleToolPool(pool_config)

    # Build loop config (with stubs for not-yet-implemented phases)
    loop_config = LoopConfig(
        system_prompt=getSystemPrompt(),
        user_context=getUserContext(config),
        model=config.model,
        max_turns=config.max_turns,
        tools=tools,
        permission_engine=StubPermissionEngine(),
        hooks=StubHookRegistry(),
        config=config,
        deepseek_api_key=config.deepseek_api_key,
        deepseek_base_url=config.deepseek_base_url,
    )

    # Assemble context
    system_context = getSystemContext(config)
    full_prompt, messages = assembleMessages(
        loop_config.system_prompt,
        system_context,
        loop_config.user_context,
        [{"role": "user", "content": prompt}],
    )
    loop_config.system_prompt = full_prompt

    # Run loop
    async for event in queryLoop(loop_config, messages):
        if isinstance(event, LoopTextResponse):
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

    pool_config = PoolConfig(cwd=config.cwd)
    tools = await assembleToolPool(pool_config)

    system_context = getSystemContext(config)
    system_prompt = getSystemPrompt()

    print(f"d2c ({config.model})")
    print("Type 'exit' or press Ctrl+C to quit.")
    print()

    while True:
        try:
            prompt = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not prompt:
            continue
        if prompt.lower() in ("exit", "quit", "q"):
            break

        loop_config = LoopConfig(
            system_prompt=system_prompt,
            user_context=getUserContext(config),
            model=config.model,
            max_turns=config.max_turns,
            tools=tools,
            permission_engine=StubPermissionEngine(),
            hooks=StubHookRegistry(),
            config=config,
            deepseek_api_key=config.deepseek_api_key,
            deepseek_base_url=config.deepseek_base_url,
        )

        full_prompt, messages = assembleMessages(
            loop_config.system_prompt,
            system_context,
            loop_config.user_context,
            [{"role": "user", "content": prompt}],
        )
        loop_config.system_prompt = full_prompt

        try:
            async for event in queryLoop(loop_config, messages):
                if isinstance(event, LoopTextResponse):
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
    if args.prompt:
        asyncio.run(run_headless(args.prompt, args))
    else:
        asyncio.run(run_interactive(args))


if __name__ == "__main__":
    main()
