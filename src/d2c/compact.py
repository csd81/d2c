"""Compaction pipeline — 5-layer graduated compaction. Paper Section 7.3.

The full pipeline applies shapers in order of increasing severity:
  1. Budget reduction — cap individual tool result sizes (always applied)
  2. Snip — trim oldest non-system messages, preserving task + recent context
  3. Microcompact — cache-aware tool-result pair summarization
  4. Context collapse — read-time projection with segmented summaries
  5. Auto-compact — model-generated summary replaces old history (last resort)

Each shaper is gated by the pressure threshold; the pipeline short-circuits
as soon as pressure is relieved.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import anthropic

from d2c.hooks import HookEvent

if TYPE_CHECKING:
    pass


# ── Compact config ────────────────────────────────────────────────────


@dataclass
class CompactConfig:
    """Paper: per-tool-result budget cap, pressure threshold, context window."""

    tool_result_max_chars: int = 30_000
    pressure_threshold: float = 0.85
    context_window_tokens: int = 128_000
    chars_per_token: float = 3.5
    compact_model: str | None = None  # None = use same model as main loop

    # Shaper 2 (Snip) settings
    snip_keep_last: int = 10  # Number of recent non-system messages to preserve

    # Shaper 3 (Microcompact) settings
    microcompact_summary_max_chars: int = 500  # Max chars per tool-result summary

    # Shaper 4 (Context Collapse) settings
    collapse_min_turns: int = 3  # Minimum turns before collapse is attempted
    collapse_segment_size: int = 6  # Messages per segment for summarization


# ── Shapers pipeline (paper Section 7.3) ───────────────────────────────


def applyContextShapers(
    messages: list[dict],
    compact_config: CompactConfig | None,
) -> list[dict]:
    """Apply budget reduction (shaper 1). Always applied.

    The full 5-layer pipeline is available via applyFullContextShapers()
    which should be called from the async loop.
    """
    if compact_config is None:
        return messages

    # Shaper 1: Budget reduction (always)
    messages = applyBudgetReduction(messages, compact_config)

    return messages


async def applyFullContextShapers(
    messages: list[dict],
    loop_config: Any,  # LoopConfig
    system_tokens: int | None = None,
) -> list[dict]:
    """Run the full 5-layer compaction pipeline, gating each layer by pressure.

    Paper Section 7.3 — graduated compaction:
      1. Budget reduction (always)
      2. Snip (when over pressure)
      3. Microcompact (when still over pressure, cache-aware)
      4. Context collapse (when still over pressure, read-time projection)
      5. Auto-compact (last resort, model-generated summary)

    Each shaper is gated by the pressure threshold; the pipeline
    short-circuits as soon as pressure is relieved.

    Phase 30: system_tokens enables cache-aligned snip/collapse boundaries.
    """
    compact_config = getattr(loop_config, "compact_config", None)
    if compact_config is None:
        return messages

    # Shaper 1: Budget reduction (always applied)
    messages = applyBudgetReduction(messages, compact_config)

    # Shaper 2: Snip (gated by pressure, Phase 30: cache-aligned)
    if checkPressure(messages, compact_config):
        messages = applySnip(messages, compact_config, system_tokens=system_tokens)

    # Shaper 3: Microcompact (gated by pressure, Phase 29: LLM summarization)
    if checkPressure(messages, compact_config):
        messages = await applyMicrocompact(messages, loop_config)

    # Shaper 4: Context collapse (gated by pressure, Phase 29+30: LLM + cache-aligned)
    if checkPressure(messages, compact_config):
        messages = await applyContextCollapse(messages, loop_config, system_tokens=system_tokens)

    # Shaper 5: Auto-compact (last resort, model-generated)
    if checkPressure(messages, compact_config):
        messages = await autoCompact(messages, loop_config)

    return messages


def checkPressure(
    messages: list[dict],
    compact_config: CompactConfig,
) -> bool:
    """Return True if messages exceed pressure threshold and need compaction."""
    tokens = estimate_tokens(messages, compact_config)
    limit = compute_pressure_limit(compact_config)
    return tokens > limit


# ── Shaper 1: Budget reduction ─────────────────────────────────────────


def applyBudgetReduction(
    messages: list[dict],
    config: CompactConfig,
) -> list[dict]:
    """Cap individual tool result sizes at configurable limit.

    Paper: "Individual tool results are capped at a configurable size,
    preventing a single verbose output from consuming disproportionate context."
    """
    result: list[dict] = []
    for msg in messages:
        if msg.get("role") == "tool" and isinstance(msg.get("content"), str):
            content = msg["content"]
            if len(content) > config.tool_result_max_chars:
                truncated = content[: config.tool_result_max_chars]
                truncated += (
                    f"\n... [truncated {len(content) - config.tool_result_max_chars} chars]"
                )
                msg = {**msg, "content": truncated}
        result.append(msg)
    return result


def _audit_shaper_applied(shaper: str, pre_count: int, post_count: int, **fields: Any) -> None:
    """Emit a compaction_shaper_applied audit event for a graduated-pipeline
    shaper (snip/microcompact/context_collapse) once it has actually
    changed the message list. Auto-compact emits its own compaction_end
    event with shaper="auto_compact" (see autoCompact below)."""
    from d2c.observability import audit

    audit(
        "compaction_shaper_applied",
        shaper=shaper,
        pre_message_count=pre_count,
        post_message_count=post_count,
        **fields,
    )


# ── Shaper 2: Snip ─────────────────────────────────────────────────────


def applySnip(
    messages: list[dict],
    config: CompactConfig,
    system_tokens: int | None = None,
) -> list[dict]:
    """Trim oldest non-system messages while preserving task and recent context.

    Paper: "snip trims older history."

    Preserves:
    - System messages (always at top)
    - First user message (the task/question)
    - Last N non-system messages (configurable via snip_keep_last)

    Phase 30: When system_tokens is provided, the cut point is adjusted
    to align with 1024-token cache block boundaries and cache_control is
    injected at the boundary.
    """
    if len(messages) <= config.snip_keep_last:
        return messages  # Nothing to snip

    keep_recent = messages[-config.snip_keep_last :]

    # Find system messages and first user message
    keep_system = [m for m in messages if m.get("role") == "system"]
    first_user = None
    for m in messages:
        if m.get("role") == "user" and m.get("content") and m not in keep_system:
            first_user = m
            break

    # Phase 30: Cache-aligned snipping
    if system_tokens is not None:
        # Build the candidate preserved prefix: system + first user
        prefix: list[dict] = list(keep_system)
        if first_user and first_user not in keep_recent:
            prefix.append(first_user)

        # Messages between prefix and keep_recent are the "middle" we trim
        # Find alignment in the preserved set (prefix + keep_recent)
        # We want to split such that prefix stays and the rest aligns to 1024
        prefix_len = len(prefix)
        # Work with the part that could be trimmed: prefix + middle
        preserveable = messages[prefix_len:]

        # Walk through preserveable to find cache-aligned split
        cumulative = system_tokens
        for m in prefix:
            cumulative += estimate_tokens([m], config)

        split_rel: int | None = None
        best_distance = CACHE_BLOCK_SIZE
        for i, m in enumerate(preserveable):
            cumulative += estimate_tokens([m], config)
            remainder = cumulative % CACHE_BLOCK_SIZE
            distance = min(remainder, CACHE_BLOCK_SIZE - remainder)
            if distance < best_distance:
                best_distance = distance
                split_rel = i

        # If we found an alignment point and there are enough tokens
        if split_rel is not None and cumulative >= CACHE_BLOCK_SIZE:
            result = list(prefix)
            kept = preserveable[split_rel:]
            if kept:
                # Inject cache_control at the alignment boundary
                kept[0] = _inject_cache_control(kept[0])
            result.extend(kept)
            _audit_shaper_applied("snip", len(messages), len(result))
            return result

    # Fallback: standard snip (no alignment or alignment not applicable)
    result = list(keep_system)
    if first_user and first_user not in keep_recent:
        result.append(first_user)
    result.extend(keep_recent)

    _audit_shaper_applied("snip", len(messages), len(result))
    return result


# ── Phase 29: LLM-based summarization helper ───────────────────────────

# Semaphore to limit concurrent summarization calls (avoid rate limits)
_summarize_semaphore = asyncio.Semaphore(3)


async def _summarize_segment_content(
    segment_text: str,
    loop_config: Any,
    summary_type: str = "tools",
) -> str:
    """Invoke a fast model to summarize a context segment.

    Falls back to character slicing if the LLM call fails, times out,
    or rate limits are hit.

    Args:
        segment_text: Raw text to summarize.
        loop_config: LoopConfig with API key, base URL, model.
        summary_type: "tools" for tool-result pairs, "history" for conversation.
    """
    if not segment_text.strip():
        return "(empty)"

    compact_model = (
        loop_config.compact_config.compact_model
        if getattr(loop_config, "compact_config", None)
        else None
    )
    model: str = compact_model or getattr(loop_config, "model", None) or "deepseek-chat"

    if summary_type == "tools":
        prompt = (
            "Summarize the following developer agent tool interaction. "
            "Keep it under 300 characters. Preserve all relevant compile errors, "
            "failing test names, parameters, file paths, and return codes:\n\n"
            f"{segment_text}"
        )
        max_chars = 300
    else:
        prompt = (
            "Summarize the conversations, tasks discussed, and decisions made in the "
            "following log. Keep it under 400 characters. Preserve file paths, "
            "tool names used, and key outcomes:\n\n"
            f"{segment_text}"
        )
        max_chars = 400

    try:
        async with _summarize_semaphore:
            client = anthropic.AsyncAnthropic(
                api_key=loop_config.deepseek_api_key,
                base_url=loop_config.deepseek_base_url,
            )
            response = await asyncio.wait_for(
                client.messages.create(
                    model=model,
                    max_tokens=150,
                    system="You are a context compression assistant. Be extremely concise.",
                    messages=[{"role": "user", "content": prompt}],
                ),
                timeout=15.0,
            )
            # Extract text from response
            text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    text += block.text
            return text.strip() or f"[summary: {segment_text[:max_chars]}]"
    except Exception:
        # Fallback: character slicing
        if len(segment_text) > max_chars:
            return segment_text[:max_chars] + "... [heuristic fallback]"
        return segment_text


# ── Shaper 3: Microcompact (Phase 29: LLM summarization) ────────────────


async def applyMicrocompact(
    messages: list[dict],
    loop_config: Any,
) -> list[dict]:
    """Phase 29: LLM-based compression of tool-result pairs into summaries.

    Groups consecutive tool-result pairs and asynchronously summarizes
    each group using a fast model. Falls back to character slicing on failure.
    Uses asyncio.Semaphore to limit concurrent API calls.
    """
    config = getattr(loop_config, "compact_config", None)
    if config is None or len(messages) < 4:
        return messages

    max_summary = config.microcompact_summary_max_chars
    result: list[dict] = []

    # Collect tool-result pair groups and schedule summaries
    summary_tasks: list[tuple[int, asyncio.Task]] = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        role = msg.get("role", "")

        if role == "system":
            result.append(msg)
            i += 1
            continue

        # Check if next messages form a tool-use → tool-result pair
        if role == "user" and i + 2 < len(messages):
            nxt = messages[i + 1]
            nnxt = messages[i + 2]
            if nxt.get("role") == "assistant" and _has_tool_use(nxt):
                if nnxt.get("role") == "tool":
                    # Collect contiguous pairs into a text segment
                    pair_count = 0
                    pair_texts: list[str] = []
                    j = i
                    while j < len(messages):
                        um = messages[j]
                        if um.get("role") != "user":
                            break
                        if j + 2 >= len(messages):
                            break
                        am = messages[j + 1]
                        tm = messages[j + 2]
                        if am.get("role") != "assistant" or not _has_tool_use(am):
                            break
                        if tm.get("role") != "tool":
                            break

                        tool_names = _get_tool_names(am)
                        tool_result_text = _content_str(tm)[:max_summary]
                        pair_texts.append(
                            f"[User: {_content_str(um)[:200]}] "
                            f"→ tools: {', '.join(tool_names)} → "
                            f"results: {tool_result_text}"
                        )
                        pair_count += 1
                        j += 3

                    if pair_count > 0:
                        segment_text = "\n".join(pair_texts)
                        # Placeholder slot — will be filled after summaries complete
                        placeholder_idx = len(result)
                        result.append({"role": "user", "content": ""})
                        # Schedule async summarization
                        task = asyncio.create_task(
                            _summarize_segment_content(
                                segment_text,
                                loop_config,
                                summary_type="tools",
                            )
                        )
                        summary_tasks.append((placeholder_idx, task))
                        i = j
                        continue

        result.append(msg)
        i += 1

    # Await all summarization tasks concurrently
    if summary_tasks:
        indices, tasks = zip(*summary_tasks)
        results_list = await asyncio.gather(*tasks, return_exceptions=True)
        for idx, summary_or_exc in zip(indices, results_list):
            if isinstance(summary_or_exc, BaseException):
                # Fallback: use char-sliced version
                summary_or_exc = ""
            prefix = "[Microcompact: summarized]\n"
            result[idx]["content"] = prefix + (str(summary_or_exc) or "(summary unavailable)")

        _audit_shaper_applied("microcompact", len(messages), len(result), pairs=len(summary_tasks))

    return result


# ── Shaper 4: Context Collapse ────────────────────────────────────────


async def applyContextCollapse(
    messages: list[dict],
    loop_config: Any,
    system_tokens: int | None = None,
) -> list[dict]:
    """Phase 29: LLM-based read-time projection over conversation history.

    Segments middle conversation turns and asynchronously summarizes
    each segment using a fast model. Preserves system messages, the
    first user message (task), and recent context.
    Falls back to char-sliced summaries on failure.

    Phase 30: When system_tokens is provided, the boundary between
    collapsed middle and recent messages is aligned to 1024-token
    cache blocks, and cache_control is injected at the boundary.
    """
    config = getattr(loop_config, "compact_config", None)
    if config is None or len(messages) < config.collapse_min_turns * 2:
        return messages

    segment_size = config.collapse_segment_size
    result: list[dict] = []

    # Preserve system messages
    system_msgs = [m for m in messages if m.get("role") == "system"]
    result.extend(system_msgs)

    # Find first user message (the task)
    first_user = None
    first_user_idx = -1
    for idx, m in enumerate(messages):
        if m.get("role") == "user" and m.get("content") and m not in system_msgs:
            first_user = m
            first_user_idx = idx
            break

    if first_user:
        result.append(first_user)

    # Collapse middle messages into segments
    skip_indices = set(range(len(system_msgs)))
    if first_user_idx >= 0:
        skip_indices.add(first_user_idx)

    # Keep last segment_size messages uncollapsed (recent context)
    keep_recent = min(segment_size, len(messages) // 3)
    recent_start = max(len(messages) - keep_recent, 0)

    # Phase 30: Cache-aligned boundary adjustment
    if system_tokens is not None:
        # Compute prefix tokens (system + first user)
        cumulative = system_tokens
        for m in result:
            cumulative += estimate_tokens([m], config)

        # Walk backwards from recent_start to find best 1024-token alignment
        best_recent_start = recent_start
        best_distance = CACHE_BLOCK_SIZE
        temp_cumulative = cumulative
        for i in range(first_user_idx + 1, len(messages)):
            if i in skip_indices:
                continue
            temp_cumulative += estimate_tokens([messages[i]], config)
            if i >= recent_start:
                remainder = temp_cumulative % CACHE_BLOCK_SIZE
                distance = min(remainder, CACHE_BLOCK_SIZE - remainder)
                if distance < best_distance:
                    best_distance = distance
                    best_recent_start = i + 1  # cut after this message
        if best_distance < CACHE_BLOCK_SIZE // 4:
            recent_start = best_recent_start

    middle = [m for i, m in enumerate(messages) if i not in skip_indices and i < recent_start]

    if middle:
        segments = _segment_messages(middle, segment_size)
        # Schedule LLM summarization for each segment concurrently
        summary_tasks: list[tuple[int, asyncio.Task]] = []
        placeholder_indices: list[int] = []

        for seg_idx, segment in enumerate(segments):
            segment_text = _summarize_segment(segment, seg_idx)
            placeholder_idx = len(result)
            placeholder_indices.append(placeholder_idx)
            result.append({"role": "user", "content": ""})
            task = asyncio.create_task(
                _summarize_segment_content(
                    segment_text,
                    loop_config,
                    summary_type="history",
                )
            )
            summary_tasks.append((placeholder_idx, task))

        # Await all summarization tasks
        if summary_tasks:
            indices, tasks = zip(*summary_tasks)
            results_list = await asyncio.gather(*tasks, return_exceptions=True)
            for idx, summary_or_exc in zip(indices, results_list):
                if isinstance(summary_or_exc, BaseException):
                    summary_or_exc = ""
                prefix = "[Context collapse: summarized]\n"
                content = str(summary_or_exc) or "(summary unavailable)"
                if idx < len(result):
                    result[idx]["content"] = prefix + content

        _audit_shaper_applied(
            "context_collapse",
            len(messages),
            len(result) + len(messages[recent_start:]),
            segments=len(segments),
        )

    # Append recent messages with cache_control injection if aligned
    recent = messages[recent_start:]
    if system_tokens is not None and recent and recent_start > 0:
        recent[0] = _inject_cache_control(recent[0])
    result.extend(recent)

    return result


# ── Segment helpers ──────────────────────────────────────────────────


def _segment_messages(messages: list[dict], segment_size: int) -> list[list[dict]]:
    """Split messages into segments of roughly segment_size each."""
    if not messages:
        return []
    segments: list[list[dict]] = []
    current: list[dict] = []
    for msg in messages:
        current.append(msg)
        if len(current) >= segment_size:
            segments.append(current)
            current = []
    if current:
        segments.append(current)
    return segments


def _summarize_segment(segment: list[dict], segment_idx: int) -> str:
    """Generate a text summary of a message segment."""
    roles: list[str] = []
    tool_names: list[str] = []
    key_content: list[str] = []

    for msg in segment:
        role = msg.get("role", "?")
        roles.append(role)
        if role == "assistant" and _has_tool_use(msg):
            tool_names.extend(_get_tool_names(msg))
        content = _content_str(msg)
        if content and len(content) > 0:
            # Capture first meaningful content
            if len(content) > 200:
                key_content.append(content[:200] + "...")
            else:
                key_content.append(content)

    role_summary = " → ".join(roles[:6])
    tool_str = f" [tools: {', '.join(tool_names[:5])}]" if tool_names else ""
    content_preview = " ".join(key_content[:2])[:300]

    return (
        f"[Context segment {segment_idx + 1}: {len(segment)} messages, "
        f"roles: {role_summary}{tool_str}]\n"
        f"{content_preview}"
    )


# ── Message content helpers ──────────────────────────────────────────


def _has_tool_use(msg: dict) -> bool:
    """Check if a message contains tool_use blocks."""
    content = msg.get("content", "")
    if isinstance(content, str):
        return False  # String content won't have structured tool_use
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                return True
    return False


def _get_tool_names(msg: dict) -> list[str]:
    """Extract tool names from a message's content blocks."""
    content = msg.get("content", "")
    if isinstance(content, list):
        return [
            block.get("name", "?")
            for block in content
            if isinstance(block, dict) and block.get("type") == "tool_use"
        ]
    return []


def _content_str(msg: dict) -> str:
    """Extract text content from a message."""
    content = msg.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
                elif block.get("type") == "tool_use":
                    parts.append(f"[tool_use: {block.get('name', '?')}]")
                elif block.get("type") == "tool_result":
                    result_content = block.get("content", "")
                    if isinstance(result_content, str):
                        parts.append(f"[result: {result_content[:100]}]")
                    else:
                        parts.append("[result]")
        return " ".join(parts)
    return str(content)


# ── Token estimation (Phase 28: BPE tokenizer) ─────────────────────────


def estimate_tokens(messages: list[dict], config: CompactConfig | None = None) -> int:
    """Precise token count using BPE tokenizer (cl100k_base).

    Delegates to context.estimate_tokens for the actual computation.
    Falls back to character-based heuristic if tiktoken is unavailable.
    """
    from d2c.context import estimate_tokens as _bpe_estimate

    chars_per_token = config.chars_per_token if config else 3.5
    return _bpe_estimate(messages, chars_per_token)


def compute_pressure_limit(config: CompactConfig) -> int:
    """Token count at which compaction triggers."""
    return int(config.context_window_tokens * config.pressure_threshold)


# ── Shaper 2: Auto-compact ─────────────────────────────────────────────


async def autoCompact(
    messages: list[dict],
    loop_config: Any,  # LoopConfig
) -> list[dict]:
    """Generate a model-compressed summary and rebuild message history.

    Paper Section 4.3 Shaper #5:
    - Build compact prompt from all but last 2 turns
    - Call model to produce compressed summary
    - Build post-compact messages: summary replaces old history
    - Record compact boundary in session store
    """
    compact_config = loop_config.compact_config
    if compact_config is None:
        return messages

    from d2c.observability import audit

    audit("compaction_start", shaper="auto_compact", pre_message_count=len(messages))

    # Phase 7: Fire PreCompact hook
    hooks = getattr(loop_config, "hooks", None)
    if hooks is not None:
        try:
            await hooks.fire(
                HookEvent.PRE_COMPACT,
                {
                    "message_count": len(messages),
                },
            )
        except Exception:
            pass  # Hook failure is non-fatal during compaction

    # Build compact prompt
    compact_prompt = getCompactPrompt(messages)

    # Call model for compaction (no tools)
    model = compact_config.compact_model or loop_config.model
    api_key = loop_config.deepseek_api_key or loop_config.config.deepseek_api_key
    if not api_key:
        return messages  # Can't compact without model access

    client = anthropic.AsyncAnthropic(
        api_key=api_key,
        base_url=loop_config.deepseek_base_url,
    )

    try:
        response = await client.messages.create(
            model=model,
            max_tokens=2048,
            system="Summarize the following conversation. Preserve key decisions, errors, file paths, and important context. Be concise but complete.",
            messages=[{"role": "user", "content": compact_prompt}],
        )
        summary = _extract_response_text(response)
    except Exception as e:
        # Compaction failure is non-fatal — continue with original messages
        audit(
            "compaction_error", level="ERROR", shaper="auto_compact", error_class=type(e).__name__
        )
        return messages

    # Build post-compact messages
    post_compact = buildPostCompactMessages(messages, summary)
    audit(
        "compaction_end",
        shaper="auto_compact",
        pre_message_count=len(messages),
        post_message_count=len(post_compact),
    )

    # Record compact boundary for persistence (paper Section 9)
    if loop_config.session_store and messages:
        last_id = ""
        for m in reversed(messages):
            if isinstance(m.get("content"), list):
                for block in m["content"]:
                    if isinstance(block, dict) and "id" in block:
                        last_id = block["id"]
                        break
            if last_id:
                break
        loop_config.session_store.append_compact_boundary(last_id)

    # Phase 15: Fire PostCompact hook
    hooks = getattr(loop_config, "hooks", None)
    if hooks is not None:
        try:
            await hooks.fire(
                HookEvent.POST_COMPACT,
                {
                    "pre_count": len(messages),
                    "post_count": len(post_compact),
                },
            )
        except Exception:
            pass  # Hook failure is non-fatal

    return post_compact


# ── Compact prompt ─────────────────────────────────────────────────────


def getCompactPrompt(messages: list[dict]) -> str:
    """Format all but the last 2 turns as a single prompt string for compaction.

    The last 2 turns (4 messages) are kept for continuity and excluded from
    the compaction prompt.
    """
    to_compact = messages[:-4] if len(messages) > 4 else messages
    lines: list[str] = []
    for msg in to_compact:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if isinstance(content, list):
            # Extract text from content blocks
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        parts.append(str(block.get("text", "")))
                    elif block.get("type") == "tool_use":
                        parts.append(f"[tool_use: {block.get('name', '?')}]")
            content = " ".join(parts)
        truncated = str(content)[:500]
        lines.append(f"[{role}]: {truncated}")
    return "\n".join(lines)


# ── Post-compact message builder ───────────────────────────────────────


def buildPostCompactMessages(
    original_messages: list[dict],
    summary: str,
) -> list[dict]:
    """Build post-compact messages: summary + recent messages after cut point.

    Paper: "The summary feeds into buildPostCompactMessages(). Post-compact
    messages consist of the summary + recent messages after the cut point."
    """
    result: list[dict] = []

    # Keep system/user-context messages at the top
    for m in original_messages:
        if m.get("role") == "system":
            result.append(m)
        else:
            break

    # Summary as a user message
    result.append(
        {
            "role": "user",
            "content": f"[Previous conversation summary]\n{summary}",
        }
    )

    # Keep last 4 messages (roughly 2 turns) for continuity
    recent = original_messages[-4:] if len(original_messages) > 4 else original_messages
    result.extend(recent)

    return result


# ── Phase 30: Cache-aligned compaction boundaries ──────────────────────

CACHE_BLOCK_SIZE = 1024  # Anthropic prompt cache block size in tokens


def _find_cache_alignment_point(
    messages: list[dict],
    config: CompactConfig,
    system_tokens: int = 0,
) -> int | None:
    """Find the message index closest to a 1024-token boundary.

    Walks through messages accumulating token counts. Returns the index
    where cumulative tokens are closest to a multiple of CACHE_BLOCK_SIZE,
    or None if the total context is below the minimum cacheable size.

    Args:
        messages: Message list to scan.
        config: CompactConfig for token estimation parameters.
        system_tokens: Token count of system prompt + tool definitions
                       (the static prefix before messages).
    """
    cumulative = system_tokens
    best_idx: int | None = None
    best_distance = CACHE_BLOCK_SIZE  # worst possible distance

    for i, msg in enumerate(messages):
        cumulative += estimate_tokens([msg], config)
        remainder = cumulative % CACHE_BLOCK_SIZE
        distance = min(remainder, CACHE_BLOCK_SIZE - remainder)
        if distance < best_distance:
            best_distance = distance
            best_idx = i

    if cumulative < CACHE_BLOCK_SIZE:
        return None  # Not enough tokens to trigger caching

    return best_idx


def _inject_cache_control(msg: dict) -> dict:
    """Attach cache_control ephemeral to the last content block of a message.

    Returns a shallow copy with cache_control injected. If content is a
    string, it is wrapped in a single-element content-block list.
    """
    msg = dict(msg)  # shallow copy
    content = msg.get("content", "")
    if isinstance(content, list) and content:
        last = dict(content[-1])
        last["cache_control"] = {"type": "ephemeral"}
        msg["content"] = content[:-1] + [last]
    elif isinstance(content, str):
        msg["content"] = [{"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}]
    return msg


def _compute_system_tools_tokens(
    system_prompt: str,
    tool_schemas: list[dict],
    config: CompactConfig,
) -> int:
    """Estimate token count for system prompt + tool definitions.

    This is the static prefix (S) that sits before the message history
    in every API request. Knowing its size lets us align the message
    cut-points to 1024-token cache block boundaries.
    """
    tokens = 0
    # System prompt: role overhead + content
    if system_prompt:
        tokens += 4  # role overhead
        tokens += int(len(system_prompt) / config.chars_per_token)
    # Tool definitions: approximate
    for tool in tool_schemas:
        tokens += 4  # per-tool overhead
        tokens += int(len(str(tool)) / config.chars_per_token)
    # +3 framing overhead
    tokens += 3
    return tokens


# ── Helpers ────────────────────────────────────────────────────────────


def _extract_response_text(response: Any) -> str:
    """Extract text from a model response."""
    content = getattr(response, "content", "")
    if isinstance(content, str):
        return content
    texts: list[str] = []
    for block in content:
        if hasattr(block, "type") and block.type == "text":
            texts.append(block.text)
        elif isinstance(block, dict) and block.get("type") == "text":
            texts.append(block.get("text", ""))
    return "\n".join(texts)
