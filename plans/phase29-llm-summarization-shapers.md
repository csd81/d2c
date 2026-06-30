# Phase 29: Asynchronous LLM-Based Summarization for Shapers 3 & 4 (Compaction Depth)

**Paper Reference:** Section 7.3 — "Budget reduction targets individual tool outputs... snip handles temporal depth... auto-compact performs semantic compression as a last resort."

**Priority:** MEDIUM (Precision & Semantic Preservation)

## Rationale

Currently, Shaper 3 (`microcompact`) and Shaper 4 (`context collapse`) use naive character slicing (e.g. `content[:200]`) to compress tool outputs and history segments. 

This is highly problematic for software engineering:
1. **Critical Data Loss**: If a test run output is 5,000 characters and the stack trace occurs at the end, character slicing drops the error details, causing the model to lose track of why a task failed.
2. **Confusing Context**: Sliced segments don't retain semantic intent.

To add depth, we will modify these shapers to make asynchronous calls to a fast, cheap model (e.g., `deepseek-chat` or `claude-3-5-haiku`) to generate concise, high-density semantic summaries of what happened, preserving file paths, compile errors, and parameters.

---

## Files to Create/Modify

1. MODIFY `src/d2c/compact.py` — rebuild `applyMicrocompact` and `applyContextCollapse` to support async model-based summarization
2. CREATE `tests/test_semantic_compaction.py` — verify LLM-based summarization calls and async scheduling

---

## Key Design

We will run segment summarizations concurrently using `asyncio.gather` to minimize the latency impact on the user.

```
Original Tool Results:
[Bash: npm test] ─► (50 lines of logs) ─┐
                                       │ ──► [asyncio.gather] ──► [Fast LLM] ──► Compact Summary
[Read: auth.py]  ─► (300 lines of code) ─┘
```

### 1. Asynchronous Microcompaction (`applyMicrocompact`)
Instead of character-slicing tool outputs, we group contiguous tool interactions and summarize them:

```python
async def applyMicrocompact(
    messages: list[dict],
    loop_config: "LoopConfig",
) -> list[dict]:
    """Compresses consecutive tool-result pairs into LLM-generated summaries."""
    # 1. Identify groups of contiguous (user -> assistant (tool_use) -> tool (result)) messages
    # 2. Extract their raw text and schemas
    # 3. Create concurrent async tasks to summarize each group
    # 4. Replace original turns with user message: "[Summary of tool runs: ...]"
    ...
```

### 2. Asynchronous Context Collapse (`applyContextCollapse`)
For historical sections that exceed the pressure threshold, we segment them into chunks of 6 messages and compile their summaries:

```python
async def applyContextCollapse(
    messages: list[dict],
    loop_config: "LoopConfig",
) -> list[dict]:
    """Creates a read-time projection by summarizing segmented history chunks."""
    # 1. Group middle conversation turns into chunks of N messages
    # 2. Asynchronously prompt the LLM: "Summarize this part of the conversation..."
    # 3. Build projected message chain: [System] + [Summaries] + [Recent turns]
```

### 3. Summarizer Interface
We will add a helper in `compact.py` that utilizes the loop client:

```python
async def _summarize_segment_content(
    segment_text: str,
    loop_config: Any,
    summary_type: str = "tools",
) -> str:
    """Invoke a fast model to summarize a context segment."""
    client = anthropic.AsyncAnthropic(
        api_key=loop_config.deepseek_api_key,
        base_url=loop_config.deepseek_base_url,
    )
    
    prompt = (
        f"Summarize the following developer agent interaction. "
        f"Keep it under 300 characters. Preserve all relevant compile errors, "
        f"failing test names, parameters, or file paths:\n\n{segment_text}"
    ) if summary_type == "tools" else (
        f"Summarize the conversations, tasks discussed, and decisions made in the "
        f"following log. Keep it under 400 characters:\n\n{segment_text}"
    )

    try:
        response = await client.messages.create(
            model=loop_config.compact_config.compact_model or loop_config.model,
            max_tokens=150,
            system="You are a context compression assistant. Be extremely concise.",
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception as e:
        # Fallback: character slicing if LLM fails
        return segment_text[:200] + "... [heuristic fallback]"
```

---

## Edge Cases

* **Rate Limits**: If the fast summarizer hit rate limits or fails, we catch the exception and fall back to the character-slicing method so the session is never blocked.
* **Large number of segments**: If there are many segments to summarize, we limit concurrency (using `asyncio.Semaphore(3)`) to avoid hitting API rate limits.

---

## Tests

Verify the following:
* `test_microcompact_calls_summarizer`: Confirms `microcompact` triggers LLM calls for tool interactions.
* `test_context_collapse_segments_and_summarizes`: Confirms `context_collapse` segments history and queries LLM.
* `test_summarization_failure_fallback`: Confirms that if the LLM summarizer times out or fails, the code falls back to safe string slicing.
