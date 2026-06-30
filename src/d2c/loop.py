"""Agent query loop — the core while-loop that calls the model, dispatches tools,
and feeds results back. Paper Section 4, query.ts.

Pattern: while not stopped { assemble → model → gate → execute → compact }
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable

import anthropic

from d2c.tools import Tool, ToolResult, ToolUse


# ── Loop events (yielded by the async generator) ────────────────────

@dataclass
class TextResponse:
    """Model finished with text (no tool calls)."""
    text: str


@dataclass
class ToolExecutionEvent:
    """A tool was executed."""
    tool_use: ToolUse
    result: ToolResult
    stop_continuation: bool = False


@dataclass
class StopEvent:
    """The loop has stopped."""
    reason: str
    metadata: dict = field(default_factory=dict)


LoopEvent = TextResponse | ToolExecutionEvent | StopEvent


# ── Loop state ───────────────────────────────────────────────────────

@dataclass
class LoopState:
    """Single mutable state object. Whole-object replacement at each continue site
    (paper Section 4.1)."""
    messages: list[dict[str, Any]]
    turn_count: int = 0
    output_tokens_recovery_attempts: int = 0
    has_attempted_reactive_compact: bool = False
    stopped: bool = False
    stop_reason: str | None = None


# ── Loop config ──────────────────────────────────────────────────────

class StubHookRegistry:
    """No-op hook registry for Phase 2. Phase 7 replaces this."""

    async def fire(self, event: str, *args: Any, **kwargs: Any) -> "StubHookResult":
        return StubHookResult()


class StubHookResult:
    decision: str | None = None
    updated_input: dict | None = None
    additional_context: str | None = None
    veto: bool = False
    error: str | None = None


class StubPermissionEngine:
    """Allow-all permission engine for Phase 2. Phase 3 replaces this."""

    async def authorize(self, tool_name: str, tool_input: dict, tool_category: str) -> str:
        return "allow"


@dataclass
class LoopConfig:
    """Immutable parameters (paper Section 4.1 step 1)."""
    system_prompt: str
    user_context: str
    model: str
    max_turns: int
    tools: list[Tool]
    permission_engine: Any  # Phase 3
    hooks: Any              # Phase 7
    config: Any             # Config
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com/anthropic"
    compact_config: Any = None  # Phase 5
    session_store: Any = None   # Phase 4


# ── Anthropic message format helpers ─────────────────────────────────

def _content_to_text(content: Any) -> str:
    """Extract text from an Anthropic content block or string."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return block.get("text", "")
    return str(content)


def _extract_tool_uses(response: Any) -> list[ToolUse]:
    """Extract tool_use blocks from a model response."""
    tool_uses: list[ToolUse] = []
    content = getattr(response, "content", [])
    if isinstance(content, str):
        return []
    for block in content:
        if hasattr(block, "type") and block.type == "tool_use":
            tool_uses.append(ToolUse(
                id=block.id,
                name=block.name,
                input=dict(block.input) if block.input else {},
            ))
    return tool_uses


def _response_text(response: Any) -> str:
    """Extract text from a model response."""
    content = getattr(response, "content", "")
    if isinstance(content, str):
        return content
    texts = []
    for block in content:
        if hasattr(block, "type") and block.type == "text":
            texts.append(block.text)
    return "\n".join(texts)


def _build_anthropic_messages(messages: list[dict]) -> list[dict]:
    """Convert our internal message format to Anthropic API format."""
    result = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if role == "tool":
            # Tool results in Anthropic format are user messages with tool_result blocks
            result.append({
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": msg.get("tool_use_id", ""),
                        "content": content if isinstance(content, str) else str(content),
                    }
                ],
            })
        elif role == "assistant" and isinstance(content, list):
            # Already in Anthropic content-block format
            result.append({"role": "assistant", "content": content})
        else:
            result.append({
                "role": role,
                "content": content,
            })
    return result


def _assistant_message_with_tools(text: str, tool_uses: list[ToolUse]) -> dict:
    """Build an assistant message with text and tool_use blocks."""
    content: list[dict] = []
    if text:
        content.append({"type": "text", "text": text})
    for tu in tool_uses:
        content.append({
            "type": "tool_use",
            "id": tu.id,
            "name": tu.name,
            "input": tu.input,
        })
    return {"role": "assistant", "content": content}


def _tool_result_message(tool_use: ToolUse, result: ToolResult) -> dict:
    """Build a tool result message in our internal format."""
    return {
        "role": "tool",
        "content": result.output,
        "tool_use_id": tool_use.id,
        "tool_name": tool_use.name,
    }


# ── Tool dispatch ────────────────────────────────────────────────────

def partitionToolCalls(tool_uses: list[ToolUse], tools_map: dict[str, Tool]) -> list[list[ToolUse]]:
    """Partition into concurrent-safe groups.

    Read-only tools run in parallel; writes serialize.
    (paper Section 4.2)
    """
    partitions: list[list[ToolUse]] = []
    current_group: list[ToolUse] = []

    for tu in tool_uses:
        tool = tools_map.get(tu.name)
        if tool and tool.is_concurrent_safe:
            current_group.append(tu)
        else:
            if current_group:
                partitions.append(list(current_group))
                current_group = []
            partitions.append([tu])

    if current_group:
        partitions.append(list(current_group))

    return partitions


async def _execute_one_tool(
    tu: ToolUse,
    tools_map: dict[str, Tool],
) -> ToolResult:
    """Execute a single tool. Permission checking added in Phase 3."""
    tool = tools_map.get(tu.name)
    if not tool:
        return ToolResult(
            output=f"Error: unknown tool '{tu.name}'",
            error=True,
            metadata={"unknown_tool": True},
        )

    try:
        return await tool.execute(**tu.input)
    except Exception as e:
        return ToolResult(
            output=f"Error executing tool '{tu.name}': {e}",
            error=True,
            metadata={"exception": str(e)},
        )


async def dispatchTools(
    tool_uses: list[ToolUse],
    tools_map: dict[str, Tool],
    state: LoopState,
) -> AsyncGenerator[ToolExecutionEvent, None]:
    """Execute tools in partitions. Within each partition, tools run concurrently.

    Results emitted in original order (paper: "output order stays the same
    even when tools run in parallel").
    """
    # Sibling abort: if any Bash tool errors, cancel in-flight tools
    abort_signal = asyncio.Event()

    partitions = partitionToolCalls(tool_uses, tools_map)
    results: list[tuple[ToolUse, ToolResult]] = []

    for partition in partitions:
        if abort_signal.is_set():
            break

        tasks = []
        for tu in partition:
            task = asyncio.create_task(_execute_one_tool(tu, tools_map))
            tasks.append((tu, task))

        for tu, task in tasks:
            try:
                result = await task
            except Exception as e:
                result = ToolResult(output=str(e), error=True)

            results.append((tu, result))

            # Paper: Sibling abort — if Bash tool errors, signal abort
            if tu.name == "Bash" and result.error:
                abort_signal.set()

    # Emit in original order
    for tu, result in results:
        state.messages.append(_tool_result_message(tu, result))
        yield ToolExecutionEvent(tool_use=tu, result=result)


# ── Main query loop ──────────────────────────────────────────────────

async def queryLoop(
    loop_config: LoopConfig,
    initial_messages: list[dict],
) -> AsyncGenerator[LoopEvent, None]:
    """Async generator yielding stream events.

    Pattern from paper: while not stopped { assemble → model → gate → execute → compact }
    """
    state = LoopState(messages=list(initial_messages))

    # Build tools map for fast lookup
    tools_map: dict[str, Tool] = {t.name: t for t in loop_config.tools}
    tool_schemas = [t.to_api_format() for t in loop_config.tools]

    # Build Anthropic client pointed at DeepSeek
    api_key = loop_config.deepseek_api_key or loop_config.config.deepseek_api_key
    if not api_key:
        yield TextResponse(text="Error: DEEPSEEK_API_KEY environment variable is required.")
        state.stopped = True
        state.stop_reason = "no_api_key"
        return

    client = anthropic.AsyncAnthropic(
        api_key=api_key,
        base_url=loop_config.deepseek_base_url,
    )

    while not state.stopped:
        # --- Context shaping ---
        # Phase 5 will add compaction pipeline here
        messages_for_query = state.messages

        # Build Anthropic-format messages
        anthropic_messages = _build_anthropic_messages(messages_for_query)

        # --- Model call ---
        try:
            response = await client.messages.create(
                model=loop_config.model,
                max_tokens=8192,
                system=loop_config.system_prompt,
                messages=anthropic_messages,
                tools=tool_schemas,
            )
        except anthropic.BadRequestError as e:
            if "prompt too long" in str(e).lower() or "too many tokens" in str(e).lower():
                if not state.has_attempted_reactive_compact:
                    # Phase 5: reactive compact
                    state.has_attempted_reactive_compact = True
                    # For now: truncate old messages
                    if len(state.messages) > 10:
                        state.messages = state.messages[:2] + [
                            {"role": "user", "content": "[Earlier conversation truncated due to length]"}
                        ] + state.messages[-6:]
                    continue
                state.stopped = True
                state.stop_reason = "prompt_too_long"
                yield StopEvent(reason="prompt_too_long")
                break
            yield TextResponse(text=f"API error: {e}")
            state.stopped = True
            state.stop_reason = "api_error"
            break
        except Exception as e:
            yield TextResponse(text=f"Error calling model: {e}")
            state.stopped = True
            state.stop_reason = "api_error"
            break

        # --- Check for text-only response (primary stop condition) ---
        tool_uses = _extract_tool_uses(response)
        if not tool_uses:
            # Paper: run stop hooks; stub for now
            text = _response_text(response)
            state.messages.append({"role": "assistant", "content": text})
            state.stopped = True
            state.stop_reason = "model_finished"
            yield TextResponse(text=text)
            break

        # --- Tool dispatch ---
        text = _response_text(response)
        state.messages.append(_assistant_message_with_tools(text, tool_uses))

        async for event in dispatchTools(tool_uses, tools_map, state):
            yield event

            # Phase 7: Hook intervention check (hook_stopped_continuation)
            if event.stop_continuation:
                state.stopped = True
                state.stop_reason = "hook_intervention"
                break

        # --- Turn limit ---
        state.turn_count += 1
        if state.turn_count >= loop_config.max_turns:
            state.stopped = True
            state.stop_reason = "max_turns"
            yield StopEvent(reason="max_turns")
