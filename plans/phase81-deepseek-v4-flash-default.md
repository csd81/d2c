# Phase 81: DeepSeek v4-flash default model

**Priority:** HIGH (cost/speed default alignment)

## Context

`d2c` currently defaults to `deepseek-v4-pro` and exposes aliases for:

- `v4` / `v4-pro` -> `deepseek-v4-pro`
- `v3` / `chat` -> `deepseek-chat`
- `r1` / `reasoner` -> `deepseek-reasoner`

The desired model surface is narrower:

- default to `deepseek-v4-flash`
- keep `deepseek-v4-pro` available
- remove `deepseek-chat` / `deepseek-reasoner` aliases and references

This makes the default faster/cheaper while keeping the stronger Pro model
available for explicit use.

## Goal

Change the supported first-class model set to:

```text
deepseek-v4-flash  default
deepseek-v4-pro    explicit stronger model
```

Supported aliases:

```text
flash, v4-flash -> deepseek-v4-flash
pro, v4, v4-pro -> deepseek-v4-pro
```

Remove user-facing support/docs for:

```text
deepseek-chat
deepseek-reasoner
v3
chat
r1
reasoner
```

## Scope

In scope:

- config default model
- model alias table
- model defaults/context window if needed
- CLI help text
- README/CLAUDE/docs references
- pricing/usage table
- tests for model resolution and CLI/list output
- changelog note

Out of scope:

- adding non-DeepSeek providers
- changing API base URL
- changing auth env var (`DEEPSEEK_API_KEY`)
- changing eval corpus semantics
- migrating old session transcripts
- full release gate

## Required Behavior

Default:

```bash
python -m d2c
```

uses:

```text
deepseek-v4-flash
```

Explicit Pro:

```bash
python -m d2c --model pro
python -m d2c --model v4-pro
python -m d2c --model deepseek-v4-pro
```

uses:

```text
deepseek-v4-pro
```

Explicit Flash:

```bash
python -m d2c --model flash
python -m d2c --model v4-flash
python -m d2c --model deepseek-v4-flash
```

uses:

```text
deepseek-v4-flash
```

Removed aliases should no longer be advertised. Decide whether they should:

1. pass through as custom model names, preserving existing `resolve_model`
   behavior, or
2. fail validation as unsupported.

Recommendation for v1: keep pass-through behavior for unknown custom model
strings, but remove all docs/tests/listing for old aliases. This avoids breaking
advanced users who intentionally pass a raw model ID.

## Files to Inspect / Modify

Likely:

```text
src/d2c/config.py
src/d2c/main.py
src/d2c/usage.py
src/d2c/tools/config_info.py
src/d2c/tools/env_info.py
README.md
CLAUDE.md
CHANGELOG.md
tests/test_phase10.py
tests/test_usage.py
tests/test_doctor.py
```

Optional:

```text
tests/test_phase81_models.py
plans/phase81-deepseek-v4-flash-default.md
```

## Pricing / Usage

Update built-in pricing defaults for `deepseek-v4-flash` and `deepseek-v4-pro`.

If exact v4-flash pricing is not already known from project docs, avoid guessing.
Use one of:

- explicit placeholder with documented override via `D2C_PRICING_*`, or
- verify current pricing from official DeepSeek/Z.ai docs before coding.

Do not silently reuse Pro pricing for Flash unless intentionally documented as a
temporary estimate.

## Tests

Add or update tests for:

1. default `Config.load()` model is `deepseek-v4-flash`
2. `resolve_model("flash") == "deepseek-v4-flash"`
3. `resolve_model("v4-flash") == "deepseek-v4-flash"`
4. `resolve_model("pro") == "deepseek-v4-pro"`
5. `resolve_model("v4-pro") == "deepseek-v4-pro"`
6. `--list-models` lists Flash and Pro only as first-class models
7. CLI help references Flash/Pro, not chat/reasoner
8. docs do not advertise `chat`, `v3`, `reasoner`, or `r1`
9. usage pricing handles Flash and Pro

If unknown custom models remain pass-through, test that behavior explicitly.

## Verification

Fast only:

```bash
./scripts/check_fast.sh
python -m pytest tests/test_phase10.py tests/test_usage.py tests/test_doctor.py
```

Manual smoke:

```bash
python -m d2c --list-models
python -m d2c --model flash "say hi"
python -m d2c --model pro "say hi"
```

Do not run the full release gate unless explicitly requested.

## Acceptance Criteria

- Default model is `deepseek-v4-flash`.
- `deepseek-v4-pro` remains supported and documented.
- Flash aliases are documented and tested.
- Old chat/reasoner aliases are removed from docs/help/list output.
- Usage/pricing behavior is updated or clearly documented as estimated.
- Headless, REPL, SDK, eval, and server paths all pick up the new default
  through shared config.
- Fast checks pass.

## Expected Outcome

New sessions use the faster/cheaper v4 Flash model by default, while Pro remains
available for harder tasks. The public model surface becomes simpler and less
confusing by removing the older chat/reasoner aliases from normal docs and UI.
