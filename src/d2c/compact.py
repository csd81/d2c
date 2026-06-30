"""Compaction pipeline — budget reduction + auto-compact. Paper Section 7.3.

Graduated compaction preserves useful information while freeing context space.
Two shapers apply in order:
  1. Budget reduction — cap individual tool result sizes
  2. Auto-compact — model-generated summary replaces old history (gated by pressure threshold)
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

import anthropic

from d2c.hooks import HookEvent

if TYPE_CHECKING:
    from d2c.loop import LoopConfig


# ── Compact config ────────────────────────────────────────────────────

@dataclass
class CompactConfig:
    """Paper: per-tool-result budget cap, pressure threshold, context window."""
    tool_result_max_chars: int = 30_000
    pressure_threshold: float = 0.85
    context_window_tokens: int = 128_000
    chars_per_token: float = 3.5
    compact_model: str | None = None  # None = use same model as main loop


# ── Shapers (paper Section 4.3) ───────────────────────────────────────

def applyContextShapers(
    messages: list[dict],
    compact_config: CompactConfig | None,
) -> list[dict]:
    """Apply budget reduction, then auto-compact if still over pressure threshold.

    Returns messages ready for the model call. Does NOT auto-compact
    synchronously — that requires an async model call handled in queryLoop.
    """
    if compact_config is None:
        return messages

    # Shaper 1: Budget reduction (always)
    messages = applyBudgetReduction(messages, compact_config)

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
                truncated = content[:config.tool_result_max_chars]
                truncated += f"\n... [truncated {len(content) - config.tool_result_max_chars} chars]"
                msg = {**msg, "content": truncated}
        result.append(msg)
    return result


# ── Token estimation ───────────────────────────────────────────────────

def estimate_tokens(messages: list[dict], config: CompactConfig | None = None) -> int:
    """Rough token estimate: total chars / chars_per_token."""
    chars_per_token = config.chars_per_token if config else 3.5
    total_chars = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            total_chars += len(json.dumps(content))
        else:
            total_chars += len(str(content))
    return int(total_chars / chars_per_token)


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

    # Phase 7: Fire PreCompact hook
    hooks = getattr(loop_config, 'hooks', None)
    if hooks is not None:
        try:
            await hooks.fire(HookEvent.PRE_COMPACT, {
                "message_count": len(messages),
            })
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
    except Exception:
        # Compaction failure is non-fatal — continue with original messages
        return messages

    # Build post-compact messages
    post_compact = buildPostCompactMessages(messages, summary)

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
    result.append({
        "role": "user",
        "content": f"[Previous conversation summary]\n{summary}",
    })

    # Keep last 4 messages (roughly 2 turns) for continuity
    recent = original_messages[-4:] if len(original_messages) > 4 else original_messages
    result.extend(recent)

    return result


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
