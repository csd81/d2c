# Phase 55: Cost and token accounting UI

**Priority:** MEDIUM-HIGH (User visibility and production operability)

## Context

`d2c` has token estimation and context-pressure handling, but it does not yet expose a full
user-facing usage/cost surface. Claude Code-style products make usage visible through status bars,
session summaries, and cost/token reporting.

This phase adds explicit token and cost accounting without changing model behavior.

## Goal

Track and display usage:

1. input tokens
2. output tokens
3. cache read/write tokens where available
4. estimated cost per model call
5. session totals
6. per-tool/output contribution where useful
7. REPL status/summary display
8. slash command for usage

## Scope

In scope:

- usage accounting data model
- model-call token extraction from provider responses
- fallback token estimation when provider usage is unavailable
- model pricing config
- session totals
- `/usage` slash command
- optional REPL status bar usage summary
- audit log usage events
- tests
- README docs

Out of scope:

- billing integration
- external telemetry
- exact provider invoice reconciliation
- optimizing prompts based on cost
- multi-currency accounting
- organization/team quotas

## Files to Inspect/Modify

1. CREATE `src/d2c/usage.py`
   - usage data types
   - pricing table
   - cost calculations
   - formatting helpers

2. MODIFY `src/d2c/loop.py`
   - record usage after model calls
   - include output-token recovery attempts
   - emit usage audit events

3. MODIFY `src/d2c/main.py`
   - add `/usage`
   - include usage in REPL state
   - optionally show compact usage in status bar

4. MODIFY `src/d2c/config.py`
   - optional pricing config overrides
   - default pricing for DeepSeek models if known

5. MODIFY `src/d2c/observability.py`
   - add `model_usage` / `session_usage` audit events

6. MODIFY `src/d2c/doctor.py`
   - optionally show model/pricing config status

7. CREATE `tests/test_usage.py`
   - unit tests for accounting and formatting

8. MODIFY `tests/test_repl_commands.py`
   - `/usage` command tests

9. MODIFY `README.md`
   - document `/usage` and estimate limitations

## Data Model

Suggested types:

```python
@dataclass
class ModelUsage:
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    estimated_cost_usd: Decimal = Decimal("0")

@dataclass
class SessionUsage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    estimated_cost_usd: Decimal = Decimal("0")
```

Use `Decimal` for money.

## Usage Extraction

Provider responses may expose usage fields. Extract if present:

```text
input_tokens
output_tokens
cache_creation_input_tokens
cache_read_input_tokens
```

If missing:

- estimate input tokens from the messages sent
- estimate output tokens from response text/tool blocks
- mark usage as estimated

Do not fail the model loop if usage extraction fails.

## Pricing

Add a pricing table:

```python
MODEL_PRICING = {
    "deepseek-v4-pro": ...,
    "deepseek-chat": ...,
    "deepseek-reasoner": ...,
}
```

If exact pricing is uncertain or changes frequently:

- make pricing configurable
- mark costs as estimates
- keep defaults conservative

Config options:

```bash
D2C_PRICING_INPUT_PER_MILLION=...
D2C_PRICING_OUTPUT_PER_MILLION=...
D2C_DISABLE_COST_ESTIMATES=1
```

Because pricing changes over time, keep docs explicit that these are estimates.

## UI

### `/usage`

Example:

```text
Session usage

Model calls: 8
Input tokens: 124,302
Output tokens: 9,184
Cache read: 64,000
Cache write: 8,192
Estimated cost: $0.42
```

### Status bar

Optional compact display:

```text
d2c | session abc123 | tokens 133k in / 9k out | est $0.42
```

Keep status bar readable. Do not crowd the REPL.

## Audit Logging

Emit:

```text
model_usage
session_usage
```

Fields:

- model
- input tokens
- output tokens
- cache tokens
- estimated cost
- estimated boolean
- session id
- turn id

No prompt text.

## Tests

Add tests for:

1. usage extraction from Anthropic-style response objects
2. fallback estimation when usage fields are absent
3. cost calculation with `Decimal`
4. unknown model produces zero/unknown cost but still tracks tokens
5. session totals accumulate across calls
6. output-token recovery attempts count as separate model calls
7. `/usage` prints totals
8. `/usage` works before any model calls
9. audit event contains tokens/cost but no prompt content
10. pricing env overrides work

## Verification

Run:

```bash
pytest tests/test_usage.py
pytest tests/test_repl_commands.py
pytest
ruff check .
ruff format --check .
mypy src/d2c
bandit -c pyproject.toml -r src/d2c
pip-audit
python -m build
twine check dist/*
```

Manual smoke:

```bash
python -m d2c
/usage
```

Then run a short prompt and check `/usage` again.

## Acceptance Criteria

- Usage is tracked per model call and per session.
- `/usage` displays readable totals.
- Costs are clearly marked as estimates.
- Missing provider usage does not break the loop.
- Pricing can be overridden or disabled.
- Audit logs include usage metadata without prompt content.
- Full gate suite remains green.

## Expected Outcome

Users can see how much context and model output a session is consuming, estimate cost, and debug
token-heavy workflows without reading raw transcripts or logs.
