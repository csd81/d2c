"""Agent query loop — the core while-loop that calls the model, dispatches tools,
and feeds results back. Paper Section 4, query.ts.

Pattern: while not stopped { assemble → model → gate → execute → compact }
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

import anthropic

from d2c.compact import (
    _compute_system_tools_tokens,
    applyContextCollapse,
    applyContextShapers,
    applyMicrocompact,
    applySnip,
    autoCompact,
    checkPressure,
)
from d2c.hooks import HookEvent, HookRegistry
from d2c.observability import audit, logs_tool_outputs
from d2c.permissions import (
    PermissionDecision,
    PermissionRequest,
)
from d2c.persistence import SessionEntry, _utc_now
from d2c.streaming_executor import StreamingToolExecutor, StreamToolParser
from d2c.tools import Tool, ToolResult, ToolUse

# ── Loop events (yielded by the async generator) ────────────────────


@dataclass
class TextResponse:
    """Model finished with text (no tool calls)."""

    text: str


@dataclass
class TextDelta:
    """Incremental text chunk from streaming response."""

    text: str
    first: bool = False  # First chunk in a turn


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


LoopEvent = TextResponse | TextDelta | ToolExecutionEvent | StopEvent


# ── Loop state ───────────────────────────────────────────────────────


@dataclass
class LoopState:
    """Single mutable state object. Whole-object replacement at each continue site
    (paper Section 4.1)."""

    messages: list[dict[str, Any]]
    turn_count: int = 0
    output_tokens_recovery_attempts: int = 0
    has_attempted_reactive_compact: bool = False  # reactive: on prompt-too-long error
    has_attempted_proactive_compact: bool = False  # proactive: pressure-triggered auto-compact
    stopped: bool = False
    stop_reason: str | None = None


# Phase 34: max escalating retries when the model truncates at the output cap
MAX_OUTPUT_TOKENS_RECOVERY = 3
BASE_MAX_TOKENS = 8192
MAX_MAX_TOKENS = 32768


# ── Loop config ──────────────────────────────────────────────────────


class StubHookRegistry(HookRegistry):
    """Legacy stub — extends HookRegistry with no hooks registered.

    Still used by tests that predate Phase 7 hook integration.
    Inherits fire() from HookRegistry but has empty hook lists.
    """


class StubPermissionEngine:
    """Legacy stub — prefer PermissionEngine in dontAsk mode for tests.

    Still used by tests that predate Phase 3 permission integration.
    """

    def evaluate(self, request):
        from d2c.permissions import PermissionResult

        return PermissionResult(PermissionDecision.ALLOW, reason="stub: allow all")

    async def evaluate_async(self, request):
        from d2c.permissions import PermissionResult

        return PermissionResult(PermissionDecision.ALLOW, reason="stub: allow all")


@dataclass
class LoopConfig:
    """Immutable parameters (paper Section 4.1 step 1)."""

    system_prompt: str
    user_context: str
    model: str
    max_turns: int
    tools: list[Tool]
    permission_engine: Any  # Phase 3
    hooks: Any  # Phase 7
    config: Any  # Config
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com/anthropic"
    compact_config: Any = None  # Phase 5
    session_store: Any = None  # Phase 4
    stream: bool = False  # Phase 10: streaming responses (opt-in)
    approval_callback: Any = None  # Phase 43: resolve ASK interactively


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
            tool_uses.append(
                ToolUse(
                    id=block.id,
                    name=block.name,
                    input=dict(block.input) if block.input else {},
                )
            )
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


def _build_anthropic_messages(
    messages: list[dict],
    enable_caching: bool = False,
) -> list[dict]:
    """Convert our internal message format to Anthropic API format.

    Phase 26: When enable_caching is True, injects cache_control breakpoints:
      - Breakpoint 3: first message (user context/CLAUDE.md)
      - Breakpoint 4: 5th-from-last message (sliding history), if > 8 messages
    """
    result = []
    total_msgs = len(messages)

    for idx, msg in enumerate(messages):
        role = msg.get("role", "user")
        content = msg.get("content", "")

        # Determine cache eligibility for this message
        cache_control = None
        if enable_caching:
            if idx == 0:
                # Breakpoint 3: Cache the user context (first message)
                cache_control = {"type": "ephemeral"}
            elif total_msgs > 8 and idx == total_msgs - 5:
                # Breakpoint 4: Sliding history cache
                cache_control = {"type": "ephemeral"}

        if role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": msg.get("tool_use_id", ""),
                "content": content if isinstance(content, str) else str(content),
            }
            if cache_control:
                block["cache_control"] = cache_control
            result.append(
                {
                    "role": "user",
                    "content": [block],
                }
            )
        elif role == "assistant" and isinstance(content, list):
            # Already in Anthropic content-block format
            formatted_content = [dict(b) for b in content]
            if cache_control and formatted_content:
                formatted_content[-1]["cache_control"] = cache_control
            result.append({"role": "assistant", "content": formatted_content})
        else:
            block = {"type": "text", "text": _content_to_text(content)}
            if cache_control:
                block["cache_control"] = cache_control
            result.append(
                {
                    "role": role,
                    "content": [block],
                }
            )
    return result


def _assistant_message_with_tools(text: str, tool_uses: list[ToolUse]) -> dict:
    """Build an assistant message with text and tool_use blocks."""
    content: list[dict] = []
    if text:
        content.append({"type": "text", "text": text})
    for tu in tool_uses:
        content.append(
            {
                "type": "tool_use",
                "id": tu.id,
                "name": tu.name,
                "input": tu.input,
            }
        )
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
    permission_engine: Any,
    hooks: Any = None,
    approval_callback: Any = None,
) -> ToolResult:
    """Execute a single tool with permission gating (Phase 3) and hooks (Phase 7)."""
    tool = tools_map.get(tu.name)
    if not tool:
        audit(
            "tool_call_error",
            level="ERROR",
            tool_name=tu.name,
            tool_call_id=tu.id,
            status="unknown_tool",
            error=True,
        )
        return ToolResult(
            output=f"Error: unknown tool '{tu.name}'",
            error=True,
            metadata={"unknown_tool": True},
        )

    _t0 = time.perf_counter()
    audit(
        "tool_call_start",
        tool_name=tu.name,
        tool_call_id=tu.id,
        category=getattr(tool.category, "value", None),
    )

    # Phase 7: PreToolUse hook
    if hooks:
        pre_result = await hooks.fire(
            HookEvent.PRE_TOOL_USE,
            {
                "tool_name": tu.name,
                "tool_input": tu.input,
            },
        )
        if pre_result.decision == "deny":
            return ToolResult(
                output=f"Hook denied: {pre_result.error or 'PreToolUse hook denied'}",
                error=True,
                metadata={"hook_denied": True},
            )
        if pre_result.updated_input:
            tu.input = pre_result.updated_input

    # Phase 3: Permission gate
    perm_request = PermissionRequest(
        tool_name=tu.name,
        tool_input=tu.input,
        tool_category=tool.category,
    )

    # Phase 38: permission gate fails CLOSED. When an engine is present but its
    # evaluation raises, the tool must not execute. (No engine at all — a
    # test-only path — skips the gate.)
    perm_result = None
    if permission_engine is not None:
        try:
            perm_result = await permission_engine.evaluate_async(perm_request)
        except Exception as e:
            if hooks:
                await hooks.fire(
                    HookEvent.PERMISSION_DENIED,
                    {
                        "tool_name": tu.name,
                        "reason": f"permission evaluation error: {type(e).__name__}",
                    },
                )
            return ToolResult(
                output=f"Permission check failed ({type(e).__name__}); denying for safety.",
                error=True,
                metadata={"denied": True, "permission_error": True},
            )
        # Phase 43: resolve ASK — never falls through to automatic execution.
        from d2c.permissions import resolve_permission_decision

        perm_result = await resolve_permission_decision(
            perm_request, perm_result, approval_callback
        )
        if perm_result is not None:
            audit(
                "permission_decision",
                tool_name=tu.name,
                tool_call_id=tu.id,
                category=getattr(tool.category, "value", None),
                decision=getattr(perm_result.decision, "value", None),
                reason=perm_result.reason,
            )

    if perm_result is not None and perm_result.decision != PermissionDecision.ALLOW:
        from d2c.permissions import PERMISSION_REQUIRED_REASON

        audit(
            "permission_denied",
            level="WARNING",
            tool_name=tu.name,
            tool_call_id=tu.id,
            reason=perm_result.reason,
        )
        result = ToolResult(
            output=f"Permission denied: {perm_result.reason}",
            error=True,
            metadata={
                "denied": True,
                "permission_required": perm_result.reason == PERMISSION_REQUIRED_REASON,
            },
        )
        # Phase 7: PermissionDenied hook
        if hooks:
            await hooks.fire(
                HookEvent.PERMISSION_DENIED,
                {
                    "tool_name": tu.name,
                    "reason": perm_result.reason,
                },
            )
        return result

    try:
        result = await tool.execute(**tu.input)

        # Phase 7: PostToolUse hook
        if hooks:
            post_result = await hooks.fire(
                HookEvent.POST_TOOL_USE,
                {
                    "tool_name": tu.name,
                    "tool_input": tu.input,
                    "tool_result": result.output,
                    "error": result.error,
                },
            )
            if post_result.updated_output:
                result.output = post_result.updated_output
            if post_result.additional_context:
                result.output += f"\n[Hook context: {post_result.additional_context}]"

        audit(
            "tool_call_end",
            tool_name=tu.name,
            tool_call_id=tu.id,
            duration_ms=round((time.perf_counter() - _t0) * 1000, 1),
            status="error" if result.error else "ok",
            error=result.error,
            output_len=len(result.output),
            output=(result.output if logs_tool_outputs() else None),
        )
        return result
    except Exception as e:
        audit(
            "tool_call_error",
            level="ERROR",
            tool_name=tu.name,
            tool_call_id=tu.id,
            duration_ms=round((time.perf_counter() - _t0) * 1000, 1),
            status="exception",
            error=True,
            error_class=type(e).__name__,
        )
        error_result = ToolResult(
            output=f"Error executing tool '{tu.name}': {e}",
            error=True,
            metadata={"exception": str(e)},
        )

        # Phase 7: PostToolUseFailure hook
        if hooks:
            await hooks.fire(
                HookEvent.POST_TOOL_USE_FAILURE,
                {
                    "tool_name": tu.name,
                    "tool_input": tu.input,
                    "error": str(e),
                },
            )

        return error_result


async def dispatchTools(
    tool_uses: list[ToolUse],
    tools_map: dict[str, Tool],
    state: LoopState,
    permission_engine: Any = None,
    session_store: Any = None,
    hooks: Any = None,
    approval_callback: Any = None,
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
            task = asyncio.create_task(
                _execute_one_tool(tu, tools_map, permission_engine, hooks, approval_callback)
            )
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
        _record(
            session_store,
            "tool",
            result.output,
            tool_name=tu.name,
            tool_use_id=tu.id,
            error=result.error,
        )
        yield ToolExecutionEvent(tool_use=tu, result=result)


# ── Main query loop ──────────────────────────────────────────────────


def _record(store, role: str, content, **metadata) -> None:
    """Record a session entry if store is available."""
    if store is None:
        return
    store.append(
        SessionEntry(
            role=role,
            content=content,
            timestamp=_utc_now(),
            entry_type="message",
            metadata=metadata,
        )
    )


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

    # Phase 30: Pre-compute system+tools token count for cache alignment
    compact_config = getattr(loop_config, "compact_config", None)
    system_tools_tokens: int | None = None
    if compact_config:
        system_tools_tokens = _compute_system_tools_tokens(
            loop_config.system_prompt,
            tool_schemas,
            compact_config,
        )

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
        # --- Context shaping (Phase 5 & 12: 5-layer compaction pipeline) ---
        compact_config = getattr(loop_config, "compact_config", None)
        if compact_config:
            # Shapers 1-4: read-time projections (non-destructive views)
            messages_for_query = applyContextShapers(state.messages, compact_config)

            # Shapers 2-4: progressive read-time shaping (gated by pressure)
            # Phase 30: pass system_tools_tokens for cache-aligned boundaries
            if checkPressure(messages_for_query, compact_config):
                messages_for_query = applySnip(
                    messages_for_query,
                    compact_config,
                    system_tokens=system_tools_tokens,
                )
            if checkPressure(messages_for_query, compact_config):
                messages_for_query = await applyMicrocompact(
                    messages_for_query,
                    loop_config,
                )
            if checkPressure(messages_for_query, compact_config):
                messages_for_query = await applyContextCollapse(
                    messages_for_query,
                    loop_config,
                    system_tokens=system_tools_tokens,
                )

            # Shaper 5: Auto-compact (destructive — mutates state, once per session)
            if checkPressure(messages_for_query, compact_config):
                if not state.has_attempted_proactive_compact:
                    state.messages = await autoCompact(state.messages, loop_config)
                    state.has_attempted_proactive_compact = True
                    messages_for_query = state.messages
        else:
            messages_for_query = state.messages

        # Phase 26: Prompt caching setup
        enable_caching = getattr(loop_config.config, "prompt_caching_enabled", False)

        # Build Anthropic-format messages with optional cache control
        anthropic_messages = _build_anthropic_messages(
            messages_for_query,
            enable_caching,
        )

        # Build system prompt: when caching, use content-block array with cache_control
        if enable_caching:
            system_param: list[dict] | str = [
                {
                    "type": "text",
                    "text": loop_config.system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        else:
            system_param = loop_config.system_prompt

        # Build tool schemas: when caching, add cache_control to the last tool
        api_tools = [dict(t) for t in tool_schemas]
        if enable_caching and api_tools:
            api_tools[-1]["cache_control"] = {"type": "ephemeral"}

        # --- Model call (Phase 10: streaming) ---
        text = ""
        tool_uses: list[ToolUse] = []
        response_stop_reason: str | None = None

        # Phase 34: escalate the output budget on each recovery attempt.
        recovery_max_tokens = min(
            BASE_MAX_TOKENS * (2**state.output_tokens_recovery_attempts),
            MAX_MAX_TOKENS,
        )

        stream_executor: StreamingToolExecutor | None = None

        audit(
            "model_call_start",
            model=loop_config.model,
            turn_id=state.turn_count,
            streaming=loop_config.stream,
            max_tokens=recovery_max_tokens,
        )
        _model_t0 = time.perf_counter()

        try:
            if loop_config.stream:
                # Streaming: yield TextDelta as chunks arrive.
                # Phase 19: Start tool execution during streaming via
                # StreamingToolExecutor to reduce latency for multi-tool responses.
                accumulated = ""
                parser = StreamToolParser()

                async with client.messages.stream(
                    model=loop_config.model,
                    max_tokens=recovery_max_tokens,
                    system=system_param,
                    messages=anthropic_messages,
                    tools=api_tools,
                ) as stream:
                    # Lazy-init executor on first tool_use start
                    async for event in stream:
                        if not hasattr(event, "type"):
                            continue

                        if event.type == "text_delta":
                            accumulated += event.text
                            yield TextDelta(text=event.text)

                        elif event.type == "content_block_start":
                            if (
                                hasattr(event.content_block, "type")
                                and event.content_block.type == "tool_use"
                            ):
                                block_id = getattr(event.content_block, "id", "")
                                block_name = getattr(event.content_block, "name", "")
                                idx = getattr(event, "index", -1)
                                if idx >= 0 and block_name:
                                    parser.feed_start(idx, block_name, block_id)

                        elif event.type == "content_block_delta":
                            if (
                                hasattr(event.delta, "type")
                                and event.delta.type == "input_json_delta"
                            ):
                                idx = getattr(event, "index", -1)
                                partial = getattr(event.delta, "partial_json", "")
                                if idx >= 0 and partial:
                                    parser.feed_delta(idx, partial)

                        elif event.type == "content_block_stop":
                            idx = getattr(event, "index", -1)
                            if idx >= 0:
                                tool_use = parser.feed_stop(idx)
                                if tool_use is not None:
                                    # Lazy-init executor and submit
                                    if stream_executor is None:
                                        stream_executor = StreamingToolExecutor(
                                            tools_map=tools_map,
                                            permission_engine=loop_config.permission_engine,
                                            hooks=loop_config.hooks,
                                            session_store=loop_config.session_store,
                                            approval_callback=loop_config.approval_callback,
                                        )
                                    stream_executor.submit(tool_use)

                # Get the complete final message from the stream
                try:
                    final_msg = await stream.get_final_message()
                    text = _response_text(final_msg)
                    tool_uses = _extract_tool_uses(final_msg)
                    response_stop_reason = getattr(final_msg, "stop_reason", None)
                except Exception:
                    # Fallback: use accumulated text
                    text = accumulated
                    tool_uses = []

                # Submit any tool_uses from final_message not already submitted during stream
                if stream_executor is not None and tool_uses:
                    submitted_ids = parser.submitted_ids
                    for tu in tool_uses:
                        if tu.id not in submitted_ids:
                            stream_executor.submit(tu)
            else:
                # Non-streaming fallback
                response = await client.messages.create(
                    model=loop_config.model,
                    max_tokens=recovery_max_tokens,
                    system=system_param,
                    messages=anthropic_messages,
                    tools=api_tools,
                )
                text = _response_text(response)
                tool_uses = _extract_tool_uses(response)
                response_stop_reason = getattr(response, "stop_reason", None)
        except anthropic.AuthenticationError as e:
            audit(
                "model_call_error",
                level="ERROR",
                turn_id=state.turn_count,
                error_class="AuthenticationError",
            )
            yield TextResponse(text=f"Authentication failed: {e}\nCheck your DEEPSEEK_API_KEY.")
            state.stopped = True
            state.stop_reason = "auth_error"
            _record(
                loop_config.session_store,
                "system",
                "",
                event="session_stop",
                stop_reason="auth_error",
            )
            break
        except anthropic.RateLimitError as e:
            audit(
                "model_call_error",
                level="ERROR",
                turn_id=state.turn_count,
                error_class="RateLimitError",
            )
            yield TextResponse(text=f"Rate limited: {e}\nWait and try again.")
            state.stopped = True
            state.stop_reason = "rate_limited"
            _record(
                loop_config.session_store,
                "system",
                "",
                event="session_stop",
                stop_reason="rate_limited",
            )
            break
        except anthropic.BadRequestError as e:
            audit(
                "model_call_error",
                level="ERROR",
                turn_id=state.turn_count,
                error_class="BadRequestError",
            )
            if "prompt too long" in str(e).lower() or "too many tokens" in str(e).lower():
                if not state.has_attempted_reactive_compact:
                    # Phase 5: reactive compact
                    state.has_attempted_reactive_compact = True
                    if len(state.messages) > 10:
                        state.messages = (
                            state.messages[:2]
                            + [
                                {
                                    "role": "user",
                                    "content": "[Earlier conversation truncated due to length]",
                                }
                            ]
                            + state.messages[-6:]
                        )
                    continue
                state.stopped = True
                state.stop_reason = "prompt_too_long"
                _record(
                    loop_config.session_store,
                    "system",
                    "",
                    event="session_stop",
                    stop_reason="prompt_too_long",
                )
                yield StopEvent(reason="prompt_too_long")
                break
            yield TextResponse(text=f"API error: {e}")
            state.stopped = True
            state.stop_reason = "api_error"
            _record(
                loop_config.session_store,
                "system",
                "",
                event="session_stop",
                stop_reason="api_error",
            )
            break
        except Exception as e:
            audit(
                "model_call_error",
                level="ERROR",
                turn_id=state.turn_count,
                error_class=type(e).__name__,
            )
            yield TextResponse(text=f"Error calling model: {e}")
            state.stopped = True
            state.stop_reason = "api_error"
            _record(
                loop_config.session_store,
                "system",
                "",
                event="session_stop",
                stop_reason="api_error",
            )
            break

        audit(
            "model_call_end",
            turn_id=state.turn_count,
            duration_ms=round((time.perf_counter() - _model_t0) * 1000, 1),
            stop_reason=response_stop_reason,
            tool_uses=len(tool_uses),
        )

        # Phase 34: output-token recovery. If the response was truncated at the
        # output cap (and isn't a tool call), retry the turn with a larger
        # budget, up to MAX_OUTPUT_TOKENS_RECOVERY attempts. Reset on success.
        if response_stop_reason == "max_tokens" and not tool_uses:
            if state.output_tokens_recovery_attempts < MAX_OUTPUT_TOKENS_RECOVERY:
                state.output_tokens_recovery_attempts += 1
                audit(
                    "output_token_recovery_retry",
                    level="WARNING",
                    turn_id=state.turn_count,
                    attempt=state.output_tokens_recovery_attempts,
                )
                continue
        else:
            state.output_tokens_recovery_attempts = 0

        # --- Check for text-only response (primary stop condition) ---
        if not tool_uses:
            state.messages.append({"role": "assistant", "content": text})
            _record(loop_config.session_store, "assistant", text)

            # Phase 7: Fire stop hooks; if vetoed, inject context and continue
            stop_result = await loop_config.hooks.fire(
                HookEvent.STOP,
                {
                    "response_text": text,
                    "turn_count": state.turn_count,
                },
            )
            if stop_result.additional_context:
                state.messages.append({"role": "user", "content": stop_result.additional_context})
            if stop_result.veto:
                continue  # Hook says keep going

            state.stopped = True
            state.stop_reason = "model_finished"
            _record(
                loop_config.session_store,
                "system",
                "",
                event="session_stop",
                stop_reason="model_finished",
            )
            yield TextResponse(text=text)
            break

        # --- Tool dispatch ---
        state.messages.append(_assistant_message_with_tools(text, tool_uses))
        # Record assistant response with tool_use blocks
        assistant_content = [{"type": "text", "text": text}] if text else []
        for tu in tool_uses:
            assistant_content.append(
                {"type": "tool_use", "id": tu.id, "name": tu.name, "input": tu.input}
            )
        _record(
            loop_config.session_store,
            "assistant",
            assistant_content if len(assistant_content) > 1 else text,
        )

        if stream_executor is not None:
            # Phase 19: Collect results from streaming executor.
            # Tools were already submitted during the stream or right after
            # get_final_message(). Collect results in original order.
            executor_results = await stream_executor.get_results()
            for tu, result in executor_results:
                state.messages.append(_tool_result_message(tu, result))
                _record(
                    loop_config.session_store,
                    "tool",
                    result.output,
                    tool_name=tu.name,
                    tool_use_id=tu.id,
                    error=result.error,
                )
                event = ToolExecutionEvent(tool_use=tu, result=result)
                yield event

                if event.stop_continuation:
                    state.stopped = True
                    state.stop_reason = "hook_intervention"
                    _record(
                        loop_config.session_store,
                        "system",
                        "",
                        event="session_stop",
                        stop_reason="hook_intervention",
                    )
                    break
        else:
            async for event in dispatchTools(
                tool_uses,
                tools_map,
                state,
                loop_config.permission_engine,
                loop_config.session_store,
                loop_config.hooks,
                loop_config.approval_callback,
            ):
                yield event

                # Phase 7: Hook intervention check (hook_stopped_continuation)
                if event.stop_continuation:
                    state.stopped = True
                    state.stop_reason = "hook_intervention"
                    _record(
                        loop_config.session_store,
                        "system",
                        "",
                        event="session_stop",
                        stop_reason="hook_intervention",
                    )
                    break

        # --- Turn limit ---
        state.turn_count += 1
        if state.turn_count >= loop_config.max_turns:
            state.stopped = True
            state.stop_reason = "max_turns"
            _record(
                loop_config.session_store,
                "system",
                "",
                event="session_stop",
                stop_reason="max_turns",
            )
            yield StopEvent(reason="max_turns")
