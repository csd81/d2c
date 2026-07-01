# Phase 35: Output-token recovery

**Paper Reference:** Section 4.4 — output-token limit recovery with bounded retries.

**Priority:** HIGH (Agent reliability)

## Context

When the model stops because it hit the output-token cap, the current turn may contain an
incomplete explanation, incomplete code, or an unfinished tool-call plan. The paper describes a
small recovery loop: retry the same model turn with a larger output budget, up to a fixed limit,
then stop if the model still cannot finish.

`LoopState.output_tokens_recovery_attempts` exists for this purpose. The implementation should make
that state meaningful in both streaming and non-streaming calls, with tests that prove the retry
path is actually exercised.

## Goal

Implement and verify bounded output-token recovery in `queryLoop()`:

1. Detect `stop_reason == "max_tokens"` from the model response.
2. Retry the same turn with a larger `max_tokens` value.
3. Cap retries at `MAX_OUTPUT_TOKENS_RECOVERY`.
4. Reset the recovery counter after any non-truncated response.
5. Avoid retrying when the response already contains tool calls.

## Files to Create/Modify

1. MODIFY `src/d2c/loop.py`
   - Use `LoopState.output_tokens_recovery_attempts` to compute the request `max_tokens`.
   - Apply the computed value to streaming and non-streaming model calls.
   - Detect `response_stop_reason == "max_tokens"` after the model call.
   - `continue` the loop after incrementing the attempt counter.
   - Reset the counter when the response is not output-truncated.

2. MODIFY or CREATE `tests/test_loop_output_recovery.py`
   - Add focused tests for recovery behavior.
   - Mock model responses so no network access is required.

3. OPTIONAL MODIFY `COMPARISON.md`
   - If implementation is already present and verified, update the audit note from "absent" to
     "implemented in Phase 35" or move it to a resolved section.

## Design

Define explicit constants near `LoopState`:

```python
BASE_MAX_TOKENS = 8192
MAX_MAX_TOKENS = 32768
MAX_OUTPUT_TOKENS_RECOVERY = 3
```

Compute the budget once per model attempt:

```python
recovery_max_tokens = min(
    BASE_MAX_TOKENS * (2 ** state.output_tokens_recovery_attempts),
    MAX_MAX_TOKENS,
)
```

Use `recovery_max_tokens` in both call paths:

```python
client.messages.create(..., max_tokens=recovery_max_tokens, ...)
client.messages.stream(..., max_tokens=recovery_max_tokens, ...)
```

After the response is available:

```python
if response_stop_reason == "max_tokens" and not tool_uses:
    if state.output_tokens_recovery_attempts < MAX_OUTPUT_TOKENS_RECOVERY:
        state.output_tokens_recovery_attempts += 1
        continue
else:
    state.output_tokens_recovery_attempts = 0
```

If the retry limit is exhausted, allow the truncated text to flow through as the final assistant
response. This keeps the user informed instead of dropping the partial result.

## Edge Cases

- **Tool calls present:** do not retry. A truncated text prefix plus tool calls should proceed
  through the normal tool-dispatch path so tool execution is not duplicated by a retry.
- **Streaming final-message failure:** if `stream.get_final_message()` fails, recovery cannot know
  the stop reason. Fall back to the accumulated text and do not retry.
- **Provider hard cap:** DeepSeek may reject or clamp large `max_tokens` values. Keep
  `MAX_MAX_TOKENS` conservative and treat provider errors through the existing API-error handling.
- **Repeated truncation:** after three retries, return the latest partial response.
- **Successful recovery:** once a complete response arrives, reset the attempt counter to zero.

## Tests

Add tests that assert:

1. A first `max_tokens` stop retries the same turn with a doubled `max_tokens` budget.
2. Recovery stops after `MAX_OUTPUT_TOKENS_RECOVERY` retries and returns the latest text.
3. A successful response resets `output_tokens_recovery_attempts`.
4. A `max_tokens` response containing tool calls does not retry and enters tool dispatch.
5. Streaming mode passes the escalated `max_tokens` value into `messages.stream`.

## Verification

Run:

```bash
pytest tests/test_loop.py tests/test_e2e.py
pytest tests/test_loop_output_recovery.py
```

If a new test file is not created, run the specific loop test file that receives the new cases.

## Acceptance Criteria

- `output_tokens_recovery_attempts` is read and written by the active loop.
- `max_tokens` changes across retries: `8192`, `16384`, `32768`, then capped.
- The retry path works without appending duplicate user messages or duplicate tool results.
- Tests cover both retry and no-retry branches.
- The audit in `COMPARISON.md` no longer claims output-token recovery is absent once the behavior
  is implemented and tested.
