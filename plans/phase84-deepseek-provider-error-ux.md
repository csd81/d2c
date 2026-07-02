# Phase 84: DeepSeek provider error UX

**Priority:** HIGH (small product-wide reliability/diagnostic win)

## Context

Phases 81-83 aligned the DeepSeek model surface, thinking controls, pricing, and
token limits. The next high-ROI DeepSeek-doc gap is provider error handling.

DeepSeek documents specific API error conditions:

```text
400 bad request / invalid format
401 authentication failed
402 insufficient balance
422 invalid parameters
429 rate limit / traffic control
500 server error
503 service unavailable / overloaded
504 gateway timeout
```

`d2c` already has basic Anthropic SDK exception handling for auth/rate-limit
paths, but user-facing messages should be DeepSeek-aware and actionable across
streaming and non-streaming calls.

## Goal

Map common DeepSeek provider failures into concise, useful user-facing messages
without changing successful request behavior.

## Scope

In scope:

- central DeepSeek/Anthropic SDK error classification helper
- clearer messages for known HTTP status codes
- consistent handling for streaming and non-streaming model calls
- tests for auth, balance, invalid params, rate/traffic, overload, timeout, and
  unknown provider failures
- docs/help troubleshooting note
- changelog note

Out of scope:

- retry/backoff policy changes
- provider failover
- Batch API
- changing model defaults or thinking semantics
- full release gate

## Proposed UX

When the provider fails, show one concise error message plus a practical next
step.

Examples:

```text
DeepSeek authentication failed (401). Check DEEPSEEK_API_KEY.
```

```text
DeepSeek balance is insufficient (402). Add credits or switch accounts.
```

```text
DeepSeek rejected the request (422). Check model, thinking, max_tokens, and tool parameters.
```

```text
DeepSeek is rate-limiting or traffic-controlling this request (429). Retry shortly.
```

```text
DeepSeek is temporarily unavailable (503). Retry shortly.
```

```text
DeepSeek timed out (504). Retry, reduce prompt/output size, or use a smaller request.
```

Unknown provider errors should still include the status code and sanitized
provider message if available.

## Error Extraction

Handle the shapes exposed by the Anthropic SDK:

- `AuthenticationError`
- `RateLimitError`
- `APIStatusError`
- `APIConnectionError`
- timeout/network exceptions if surfaced separately

Extract, when safe:

```text
status_code
provider error message
request id / response header if available
```

Do not print API keys, full request bodies, full prompts, tool inputs, or raw
response objects.

## Implementation Notes

Prefer a single helper near the model-call boundary:

```text
src/d2c/provider_errors.py
```

or a small helper inside `loop.py` if the surrounding code strongly favors that.

Suggested API:

```python
def format_provider_error(exc: BaseException) -> str:
    ...
```

Optional structured form if tests/readability benefit:

```python
@dataclass(frozen=True)
class ProviderErrorInfo:
    status_code: int | None
    kind: str
    message: str
    retryable: bool
```

Keep retryability metadata internal for now unless an existing status/event
surface has a natural place for it.

## Files to Inspect / Modify

Likely:

```text
src/d2c/loop.py
src/d2c/main.py
README.md
CHANGELOG.md
tests/test_phase10.py
tests/test_loop.py
```

Optional:

```text
src/d2c/provider_errors.py
tests/test_phase84_provider_errors.py
docs/troubleshooting.md
```

## Tests

Add or update tests for:

1. `401` auth error says to check `DEEPSEEK_API_KEY`.
2. `402` insufficient balance is distinct from auth/rate-limit.
3. `422` invalid params mentions model/thinking/max_tokens/tool parameters.
4. `429` says rate/traffic control and retry shortly.
5. `500` says provider server error and retry.
6. `503` says temporarily unavailable/overloaded and retry.
7. `504` says timeout and suggests smaller request or retry.
8. unknown status includes status code and sanitized provider message.
9. streaming path and non-streaming path use the same formatter.
10. formatted errors do not include API keys, request bodies, prompts, or tool
    inputs.

Prefer unit tests for the formatter plus one or two loop integration tests. Do
not overfit to the exact Anthropic SDK constructor if a small fake exception can
cover the same attributes more robustly.

## Verification

Fast only:

```bash
./scripts/check_fast.sh
python -m pytest tests/test_phase10.py tests/test_loop.py tests/test_phase84_provider_errors.py
```

Manual smoke:

```bash
DEEPSEEK_API_KEY=bad python -m d2c "say hi"
python -m d2c --model definitely-not-a-real-model "say hi"
```

Do not run the full release gate unless explicitly requested.

## Risks

| Risk | Mitigation |
| --- | --- |
| SDK exception shapes vary by version | Test formatter against duck-typed status/body/message attributes |
| Messages leak sensitive request data | Sanitize aggressively; never stringify full response/request objects |
| Existing tests expect old wording | Keep stable leading phrases and update only intended assertions |
| DeepSeek changes docs/status meanings | Keep status mapping small and source-linked in comments/docs |
| Network/connectivity errors get mislabeled as provider errors | Separate connection errors from HTTP status errors |

## Acceptance Criteria

- Known DeepSeek HTTP failures produce actionable messages.
- Streaming and non-streaming paths share the same formatting behavior.
- Sensitive request data is not leaked.
- Existing successful call behavior is unchanged.
- README or troubleshooting docs mention the common DeepSeek failures.
- Fast checks pass.

## Expected Outcome

Provider failures become easier to diagnose in normal CLI, REPL, Textual, SDK,
MCP, eval, and server flows. Users see whether they need to fix credentials,
add balance, adjust request parameters, or simply retry provider-side failures,
without reading raw SDK exceptions.
