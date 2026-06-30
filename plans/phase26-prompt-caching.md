# Phase 26: Explicit Prompt Caching (Cost/Performance)

**Paper Reference:** Section 7.1, 7.3, 11.6 — "Microcompact reacts to cache overhead... prompt cache expires after five minutes... budget reduction and microcompact optimize boundary messages for prompt caching."

**Priority:** HIGH (Cost & Latency Optimization)

## Rationale

The Anthropic Messages API supports prompt caching, which can reduce token costs by up to 90% and decrease latency by up to 80% for long-context agentic sessions. Currently, `d2c` does not explicitly flag cached segments.

By injecting `cache_control` blocks in system prompts, tool schemas, and history turns, we enable the official Anthropic API to reuse previously parsed contexts (such as `CLAUDE.md`, the 54+ tool schemas, and historical conversation blocks).

---

## Files to Create/Modify

1. CREATE `plans/phase26-prompt-caching.md` — this plan file
2. MODIFY `src/d2c/config.py` — add prompt caching flag to Config
3. MODIFY `src/d2c/loop.py` — inject `cache_control` headers dynamically during Anthropic API requests
4. CREATE `tests/test_prompt_caching.py` — verify cache control insertion patterns

---

## Key Design

### 1. Anthropic Prompt Caching Rules
Anthropic allows up to **4 cache breakpoints** in a single API call. Breakpoints must be placed on:
1. **System prompt**: Inside the `system` parameter block.
2. **Tool schemas**: On individual tools in the `tools` array.
3. **Messages history**: On specific content blocks inside the `messages` array.

### 2. Caching Strategy for `d2c`
We will use 3 of the 4 available cache breakpoints to optimize the prompt:
1. **Breakpoint 1 (System Prompt)**: Set on the last block of the `system` parameter.
2. **Breakpoint 2 (Tool Schemas)**: Set on the last tool in the `tools` array (caching the entire tool collection).
3. **Breakpoint 3 (User Context / CLAUDE.md)**: Set on the first message in the `messages` list (which contains `getUserContext()`).
4. **Breakpoint 4 (Sliding Conversation History)**: Set on the assistant/user message at the most recent turn boundaries (specifically, on the message that is 4 turns back) to preserve the sliding cache.

---

## Implementation Details

### 1. Config Updates (`src/d2c/config.py`)
```python
# Add to Config dataclass:
prompt_caching_enabled: bool = True  # Enabled by default
```

### 2. Injecting Cache Control in `src/d2c/loop.py`

Modify `_build_anthropic_messages` to support cache control injection:

```python
def _build_anthropic_messages(messages: list[dict], enable_caching: bool = False) -> list[dict]:
    result = []
    total_msgs = len(messages)
    
    for idx, msg in enumerate(messages):
        role = msg.get("role", "user")
        content = msg.get("content", "")
        
        # Determine cache eligibility for this message
        cache_control = None
        if enable_caching:
            # Breakpoint 3: Cache the user context (first message)
            if idx == 0:
                cache_control = {"type": "ephemeral"}
            # Breakpoint 4: Cache sliding history (e.g. 4 turns back, if conversation is long)
            elif total_msgs > 8 and idx == total_msgs - 5:
                cache_control = {"type": "ephemeral"}

        if role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": msg.get("tool_use_id", ""),
                "content": content if isinstance(content, str) else str(content),
            }
            if cache_control:
                block["cache_control"] = cache_control
            result.append({
                "role": "user",
                "content": [block],
            })
        elif role == "assistant" and isinstance(content, list):
            # Already content blocks
            formatted_content = []
            for b in content:
                formatted_content.append(dict(b))
            if cache_control and formatted_content:
                # Add cache control to the last content block of the assistant message
                formatted_content[-1]["cache_control"] = cache_control
            result.append({"role": "assistant", "content": formatted_content})
        else:
            block = {"type": "text", "text": _content_to_text(content)}
            if cache_control:
                block["cache_control"] = cache_control
            result.append({
                "role": role,
                "content": [block],
            })
            
    return result
```

Update the Anthropic API call in `queryLoop()`:

```python
        # Build Anthropic-format messages with caching if enabled
        enable_caching = loop_config.config.prompt_caching_enabled
        anthropic_messages = _build_anthropic_messages(messages_for_query, enable_caching)

        # Build system prompt parameter
        system_param = loop_config.system_prompt
        if enable_caching:
            # System prompt must be passed as an array of content blocks to attach cache_control
            system_param = [
                {
                    "type": "text",
                    "text": loop_config.system_prompt,
                    "cache_control": {"type": "ephemeral"}
                }
            ]

        # Build tool definitions with cache control on the last tool
        api_tools = [dict(t) for t in tool_schemas]
        if enable_caching and api_tools:
            api_tools[-1]["cache_control"] = {"type": "ephemeral"}
```

---

## Edge Cases

- **Non-Anthropic Backends (like DeepSeek)**: DeepSeek's API ignores `cache_control` headers gracefully, or we can strip them out when standard Anthropic models are not in use to avoid errors.
- **Very Short Conversations**: Under 4 turns, there is no need to set the sliding history breakpoint (Breakpoint 4).
- **Compacted Context**: When compaction (`autoCompact`) occurs, the history is truncated, and the new summarized context becomes the first message. The first message breakpoint (Breakpoint 3) automatically caches this new starting point.

---

## Tests (`tests/test_prompt_caching.py`)

- `test_build_messages_adds_cache_control_to_first_message`: Verify the first message gets cache control.
- `test_build_messages_adds_sliding_cache_control`: Verify the 5th-from-last message gets cache control in long histories.
- `test_query_loop_formats_system_and_tools_caching`: Mock the client calls to verify the `system` and `tools` parameters have `cache_control` injected when caching is enabled.
- `test_disable_prompt_caching`: Verify no cache control blocks are injected when `prompt_caching_enabled = False`.
