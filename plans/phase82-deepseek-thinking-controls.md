# Phase 82: DeepSeek thinking controls

**Priority:** HIGH (quality lever after v4 Flash default)

## Context

Phase 81 narrowed the first-class model surface to:

```text
deepseek-v4-flash  default
deepseek-v4-pro    explicit stronger model
```

DeepSeek's current docs expose an explicit thinking control through provider
request metadata. For Anthropic-compatible calls, thinking is enabled with
`extra_body`, including a token budget:

```python
extra_body={"thinking": {"type": "enabled", "budget_tokens": 8192}}
```

`d2c` does not currently expose this as a user-facing or config-level control.
Because thinking may affect tool/JSON behavior and latency, it should be added
as an opt-in capability rather than changing the default behavior.

## Goal

Add a clear, tested DeepSeek thinking control that can be enabled when useful
for harder tasks, while preserving the fast `deepseek-v4-flash` default path.

## Scope

In scope:

- config/env/CLI support for thinking mode
- provider request plumbing for Anthropic-compatible DeepSeek calls
- budget mapping for simple presets
- docs/help/list output updates
- usage/cost display updates if DeepSeek returns thinking usage fields
- tests proving default behavior is unchanged
- changelog note

Out of scope:

- making thinking the default
- adding non-DeepSeek providers
- changing the default model away from `deepseek-v4-flash`
- changing approval, sandbox, or tool semantics
- full release gate
- exposing raw hidden reasoning in logs/transcripts unless explicitly safe and
  documented

## Proposed UX

Add a simple setting with conservative defaults:

```text
thinking: off
```

Accepted values:

```text
off
low
medium
high
```

Recommended budget mapping:

```text
off     no thinking extra_body
low     4096 budget_tokens
medium  8192 budget_tokens
high    16384 budget_tokens
```

If DeepSeek documents better current values during implementation, prefer the
official values and update this plan in the implementation notes.

## User-Facing Controls

CLI:

```bash
python -m d2c --thinking off
python -m d2c --thinking low
python -m d2c --thinking medium
python -m d2c --thinking high
```

Environment:

```bash
D2C_THINKING=medium python -m d2c
```

Config:

```yaml
model: deepseek-v4-pro
thinking: medium
```

Precedence should match existing config style:

```text
CLI > env > config > default off
```

## Provider Behavior

Default/off:

- Do not send a thinking payload.
- Preserve the existing request shape exactly where practical.
- Preserve existing tool behavior.

Enabled:

- Send provider metadata through the Anthropic-compatible client:

```python
extra_body={"thinking": {"type": "enabled", "budget_tokens": budget}}
```

- Keep model resolution separate from thinking mode.
- If a model rejects thinking, show a clear provider/config error instead of
  silently retrying with different semantics.

## Rendering / Transcript Policy

Do not dump hidden reasoning into normal transcripts by default.

If DeepSeek returns separate thinking content:

- count it in usage if usage fields are available
- optionally summarize as metadata such as `thinking: medium`
- do not print raw chain-of-thought unless a later explicit design adds a safe
  visible reasoning mode

If DeepSeek returns visible `<think>...</think>` blocks in normal content:

- handle them deliberately
- prefer hiding or compacting them in interactive UI unless they are required for
  correctness
- add tests for whatever behavior is chosen

## Files to Inspect / Modify

Likely:

```text
src/d2c/config.py
src/d2c/main.py
src/d2c/loop.py
src/d2c/usage.py
src/d2c/tools/config_info.py
src/d2c/tools/env_info.py
README.md
CLAUDE.md
CHANGELOG.md
```

Optional:

```text
tests/test_phase82_thinking.py
tests/test_usage.py
tests/test_doctor.py
docs/deepseek.md
```

## Tests

Add or update tests for:

1. default config resolves `thinking == "off"`
2. env override accepts `D2C_THINKING=low|medium|high|off`
3. CLI override wins over env/config
4. invalid thinking values fail clearly
5. default/off request sends no `extra_body.thinking`
6. enabled request sends the expected `budget_tokens`
7. headless, REPL, Textual, SDK, MCP, eval, and server paths inherit the shared
   config without separate defaults
8. usage display remains stable when no thinking usage fields are returned
9. docs/help mention that thinking is opt-in and may affect latency/cost

If raw `<think>` blocks are observed in mocked/provider responses, add tests for
the chosen render policy.

## Verification

Fast only:

```bash
./scripts/check_fast.sh
python -m pytest tests/test_phase82_thinking.py tests/test_usage.py tests/test_doctor.py
```

Manual smoke:

```bash
python -m d2c --thinking off "say hi"
python -m d2c --model pro --thinking medium "reason about a small design tradeoff"
D2C_THINKING=low python -m d2c --model pro "say hi"
```

Do not run the full release gate unless explicitly requested.

## Risks

| Risk | Mitigation |
| --- | --- |
| Thinking breaks tool calling for a model | Keep default off; test tool request shape; show explicit provider error |
| Hidden reasoning leaks into transcripts | Do not render raw thinking by default; test `<think>` handling if needed |
| Latency/cost surprise | Document opt-in behavior and show thinking mode in config/status |
| Provider schema changes | Centralize request-body construction and keep invalid-value tests |
| Model naming ambiguity | Keep model selection separate from thinking; do not auto-switch models silently |

## Acceptance Criteria

- Thinking is off by default.
- Users can enable thinking via CLI, env, and config.
- Request payloads include DeepSeek thinking metadata only when enabled.
- Existing default tool behavior remains unchanged.
- Invalid settings fail with a clear message.
- Usage/status/docs make the active thinking mode visible.
- Raw hidden reasoning is not leaked by default.
- Fast checks pass.

## Expected Outcome

`d2c` gains a controlled quality lever for harder DeepSeek runs without slowing
or destabilizing the default Flash workflow. Users can opt into extra reasoning
when they need it, and the implementation keeps provider-specific thinking logic
small, explicit, and tested.
