# Phase 30: Cache-Aligned Compaction Boundaries (Compaction Depth)

**Paper Reference:** Section 4.3, 7.3, 11.6 — "Microcompact reacts to cache overhead... cache-aware compaction avoids invalidating prompt caches during compression... prompt cache expires after five minutes of inactivity."

**Priority:** MEDIUM (Performance & Cost Optimization)

## Rationale

Anthropic's prompt caching operates on blocks of **1024 tokens**. If we snip or collapse history at arbitrary positions, we run the risk of shifting the remaining messages out of cache alignment. Furthermore, editing historical messages (which `microcompact` and `context collapse` do by replacing messages with string summaries) invalidates all cached tokens after the edit point.

To solve this, we must align our compaction shapers with BPE token boundaries. By truncating or summarizing *behind* a cache-breakpoint boundary that matches a multiple of 1024 tokens, we ensure that the static prefix (System Prompt + Tools + Consolidated History Summary) is locked as a cache hit, while only the active sliding window (recent turns) is processed as new input.

---

## Files to Create/Modify

1. MODIFY `src/d2c/compact.py` — add cache alignment calculations to `applySnip` and `applyContextCollapse`
2. MODIFY `src/d2c/loop.py` — integrate alignment checks prior to calling compaction shapers
3. CREATE `tests/test_cache_alignment.py` — verify 1024-token boundary offsets

---

## Key Design

### 1. The 1024-Token Breakpoint Calculator
We need to calculate where to split the message history. Given a list of messages:
1. Count the tokens of the System Prompt + Tool definitions. Let's call this `S`.
2. Iterate through the message history. Count cumulative tokens: `T_i = S + sum(tokens(msg_0) ... tokens(msg_i))`.
3. Find the index `k` where `T_k` is closest to a multiple of 1024 tokens (i.e. `T_k % 1024` is minimized or falls within a safe buffer).
4. Perform the compaction (slicing/snapping) exactly at index `k`.

```
System + Tools (S tokens) ──► Msg 0 ──► Msg 1 ... ──► [Align point: mult of 1024] ──► Msg K (Cache Control)
                                                       │
                                            Compaction Cut Boundary
```

### 2. Injecting Cache Control Tag
At the alignment boundary message (index `k`), we append `"cache_control": {"type": "ephemeral"}` to the last content block. This tells the API to save the cache state exactly up to this point.

### 3. Modifying `applySnip` with Alignment
```python
def applySnipAligned(
    messages: list[dict],
    config: CompactConfig,
    system_and_tools_tokens: int,
) -> list[dict]:
    """Snips history at a boundary aligned with 1024-token blocks."""
    # 1. Calculate cumulative tokens starting from system_and_tools_tokens
    # 2. Identify the message index 'split_idx' in history (before the keep_last window)
    #    where the cumulative count is closest to a 1024-token boundary
    # 3. Slice history: keep system + first task + messages[split_idx:]
    # 4. Attach cache_control to messages[split_idx]
    ...
```

---

## Edge Cases

* **Total context is too small**: If the entire payload (including system + tools + history) is less than 1024 tokens, prompt caching is not available (Anthropic requires a minimum of 1024 tokens to cache). The alignment step no-ops.
* **Large single message**: If a single tool output is huge (e.g. 5,000 tokens), it will span multiple 1024-token blocks. The calculator should align to the nearest block after the tool result.

---

## Tests

Verify the following:
* `test_find_cache_alignment_point`: Confirms splitting logic correctly identifies message indices that minimize remainder modulo 1024.
* `test_aligned_compaction_cache_control_insertion`: Confirms the `cache_control` block is injected at the split message.
* `test_compaction_no_op_below_cache_threshold`: Confirms alignment is skipped if the total token count is under 1024.
