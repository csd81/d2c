# Phase 83: DeepSeek official limits and pricing alignment

**Priority:** HIGH (correct defaults, cost display, and output budgets)

## Context

Phases 81 and 82 handled the default model surface and opt-in thinking control.
The remaining high-ROI DeepSeek-doc gap is official provider alignment:

- DeepSeek docs list `deepseek-v4-flash` as the fast/default serverless model.
- DeepSeek docs list the paid stronger serverless model as `deepseek-v4`.
- Serverless pricing currently documents `deepseek-v4-flash` as free
  (`$0` input / cached input / output) and `deepseek-v4` as paid.
- Serverless docs show `deepseek-v4-flash` / `deepseek-v4` with 128K context and
  32K max output.

The repo now defaults to `deepseek-v4-flash`, but source inspection still shows
old `8192` max-output defaults and Phase 81 intentionally left Flash pricing as
an estimate. This phase should replace estimates with official values and make
the request caps match the provider's documented limits.

## Goal

Align DeepSeek model metadata with official docs:

1. Use official pricing for Flash and the paid stronger model.
2. Use documented max-output limits in config and loop recovery.
3. Verify the actual paid model ID used by `pro`.
4. Keep user-facing aliases simple: `flash` and `pro`.

## Scope

In scope:

- model default metadata (`max_tokens`, `context_window`)
- loop output-token recovery base/cap
- usage pricing table and cost display tests
- `--list-models` output
- docs/help/CLAUDE/README updates
- optional live smoke to verify accepted model IDs
- changelog note

Out of scope:

- changing the default away from `deepseek-v4-flash`
- adding non-DeepSeek providers
- Batch API
- thinking UX changes beyond ensuring caps do not conflict
- full release gate

## Model-ID Decision

Official docs should be treated as the source of truth, but avoid breaking users
unnecessarily.

Investigate:

```text
deepseek-v4-pro
deepseek-v4
```

Decision rule:

- If `deepseek-v4-pro` is accepted by the current API and is intentionally used
  by this project, keep `pro -> deepseek-v4-pro` but document that pricing is
  tied to the corresponding paid v4 tier.
- If `deepseek-v4-pro` is not accepted or not official, change the canonical
  mapping to `pro -> deepseek-v4`, keep `v4-pro` as a compatibility alias if
  needed, and update docs/tests.

Do not silently map user input to a different paid model without tests and docs.

## Pricing

Replace Phase 81 estimates with official pricing values from DeepSeek docs.

Expected serverless pricing:

```text
deepseek-v4-flash input:  $0.000 / 1M
deepseek-v4-flash cache:  $0.000 / 1M
deepseek-v4-flash output: $0.000 / 1M

deepseek-v4 input:        $0.280 / 1M
deepseek-v4 cache:        $0.028 / 1M
deepseek-v4 output:       $0.420 / 1M
```

If the project keeps canonical `deepseek-v4-pro`, either:

- map it to the same paid v4 pricing with an explicit comment, or
- use a distinct official pricing source if one exists.

Remove comments that call Flash pricing an estimate once official values are
encoded.

## Limits

Expected serverless limits:

```text
context_window: 128_000
max_tokens:     32_000
```

Update:

- `DEEPSEEK_MODEL_DEFAULTS`
- config default propagation
- loop base max output budget
- output-token recovery cap
- tests that currently assert `8192`
- `--list-models` output

Keep recovery bounded. Recommended behavior:

```text
base max_tokens: 32_000
recovery cap:    32_000
```

If recovery still needs escalation for legacy/custom models, make the base/cap
come from resolved model defaults rather than a global `8192` constant.

## Files to Inspect / Modify

Likely:

```text
src/d2c/config.py
src/d2c/loop.py
src/d2c/usage.py
src/d2c/main.py
src/d2c/tools/config_info.py
src/d2c/tools/env_info.py
README.md
CLAUDE.md
CHANGELOG.md
tests/test_phase10.py
tests/test_phase81_models.py
tests/test_loop_output_recovery.py
tests/test_usage.py
```

Optional:

```text
tests/test_phase83_deepseek_metadata.py
docs/deepseek.md
```

## Tests

Add or update tests for:

1. Flash max output is 32K.
2. Pro/paid v4 max output is 32K.
3. Context window remains 128K for both first-class models.
4. `--list-models` reports the updated limits.
5. Flash cost computes to zero for input/cache/output usage.
6. Paid v4/pro cost computes from official pricing.
7. Output-token recovery uses model metadata rather than stale global `8192`
   assumptions.
8. Custom/unknown model fallback remains safe.
9. Thinking mode still sends the selected budget and does not alter model limits
   unexpectedly.

## Verification

Fast only:

```bash
./scripts/check_fast.sh
python -m pytest tests/test_phase10.py tests/test_phase81_models.py tests/test_phase82_thinking.py tests/test_loop_output_recovery.py tests/test_usage.py
```

Manual smoke:

```bash
python -m d2c --list-models
python -m d2c --model flash "say hi"
python -m d2c --model pro "say hi"
python -m d2c --model pro --thinking medium "say hi"
```

Optional model-ID verification, only if `DEEPSEEK_API_KEY` is available:

```bash
python -m d2c --model deepseek-v4 "say hi"
python -m d2c --model deepseek-v4-pro "say hi"
```

Do not run the full release gate unless explicitly requested.

## Risks

| Risk | Mitigation |
| --- | --- |
| `deepseek-v4-pro` differs from official `deepseek-v4` | Verify live or document compatibility alias clearly |
| 32K output increases latency on long generations | Keep user-configurable max tokens if already supported; document behavior |
| Zero-cost Flash display hides non-model costs | Scope cost display to model API usage only |
| Provider docs change again | Keep official source link/comment near pricing metadata |
| Recovery tests become brittle | Test model-derived budgets rather than magic constants where possible |

## Acceptance Criteria

- Built-in Flash pricing is official zero pricing, not an estimate.
- Paid v4/pro pricing is official or explicitly mapped with a comment.
- First-class model max output defaults are 32K.
- Output-token recovery no longer depends on stale 8192 assumptions for first-class
  DeepSeek v4 models.
- `--list-models`, docs, and tests reflect official limits/pricing.
- Pro model ID decision is explicit and tested.
- Fast checks pass.

## Expected Outcome

`d2c` reports accurate DeepSeek costs and sends requests with output budgets that
match the current provider limits. The model surface remains simple for users,
while the internals stop carrying stale pricing and max-token assumptions from
older DeepSeek models.
