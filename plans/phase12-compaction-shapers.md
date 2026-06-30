# Phase 12: 3 Missing Compaction Shapers

**Paper Reference:** Section 7.3 — five-layer compaction pipeline

**Priority:** HIGH

## Rationale

We only have shapers 1 (budget reduction) and 5 (auto-compact). The paper's graduated
5-layer pipeline is one of the most architecturally significant components. Missing shapers
2-4 reduce context management effectiveness. The full pipeline: budget reduction → snip →
microcompact → context collapse → auto-compact.

## Files to Modify

1. MODIFY `src/d2c/compact.py` — add three new shapers

## Shaper 2: Snip (`applySnip`)

Trims oldest messages while preserving system instructions and recent context.

```python
def applySnip(messages, config) -> list[dict]:
    """
    Paper: "snip trims older history."
    Removes oldest non-system messages when over token budget.
    Preserves: system messages, first user message (task), last N messages.
    """
    keep_system = [m for m in messages if m["role"] == "system"]
    keep_recent = messages[-config.snip_keep_last:]
    task_msg = None
    for m in messages:
        if m["role"] == "user" and m.get("content"):
            task_msg = m
            break
    if task_msg and task_msg not in keep_recent:
        return keep_system + [task_msg] + keep_recent
    return keep_system + keep_recent
```

## Shaper 3: Microcompact (`applyMicrocompact`)

Cache-aware compression that avoids invalidating prompt caches during compression.

```python
async def applyMicrocompact(messages, loop_config) -> list[dict]:
    """
    Paper: "The cache-aware behavior of microcompact adds further opacity,
    as compression decisions are influenced by prompt caching."

    Compresses old tool results into brief summaries without breaking
    the Anthropic prompt cache prefix. Groups tool-result pairs into
    summaries at safe cache break points (system message boundaries,
    non-tool messages).
    """
    # Identify safe break points
    # Summarize tool result pairs between break points
    # Replace with compact "tool executed: summary" messages
    ...
```

## Shaper 4: Context Collapse (`applyContextCollapse`)

Read-time projection replacing full history with a model-generated summary.

```python
async def applyContextCollapse(messages, loop_config) -> list[dict]:
    """
    Paper: "context collapse substitutes messages with a summary
    (described in the source as 'a read-time projection over the REPL's
    full history')."

    Unlike auto-compact which replaces history, context collapse
    creates a read-time view. The full transcript on disk is preserved,
    but the model sees only the collapsed view.
    """
    # Segment conversation into logical chunks (by task/topic)
    # Generate per-segment summaries
    # Build projected view: system + summaries + recent messages
    # Full history remains in session_store for audit/resume
    ...
```

## Integration

Update `applyContextShapers()` to run all 5 shapers in order:

```python
def applyContextShapers(messages, loop_config, hooks):
    # 1. Budget reduction (always)
    messages = applyBudgetReduction(messages, loop_config.compact_config)
    # 2. Snip (when over pressure threshold)
    if over_pressure(messages, loop_config):
        messages = applySnip(messages, loop_config.compact_config)
    # 3. Microcompact (when still over threshold, cache-aware)
    if over_pressure(messages, loop_config):
        messages = await applyMicrocompact(messages, loop_config)
    # 4. Context collapse (when still over threshold)
    if over_pressure(messages, loop_config):
        messages = await applyContextCollapse(messages, loop_config)
    # 5. Auto-compact (last resort, model-generated summary)
    if over_pressure(messages, loop_config):
        messages = await autoCompact(messages, loop_config)
    return messages
```

## Edge Cases

- Snip on very short conversations → no-op
- Microcompact with zero tool results → skip
- Context collapse with < 3 turns → skip (nothing meaningful to collapse)
- Collapse preserves hook-injected context
- Cache break detection: respect Anthropic's 1024-token cache break boundaries

## Tests (~15)

- Snip trims oldest non-system messages
- Snip preserves task message
- Snip preserves last N configurable messages
- Microcompact summarizes tool-result pairs
- Microcompact respects cache break boundaries
- Context collapse produces read-time projection
- Context collapse preserves full transcript on disk
- applyContextShapers runs all 5 in order
- Each shaper no-ops on under-pressure input
- Shaper pipeline short-circuits when pressure relieved
