# Phase 58: WebSearch provider expansion

**Priority:** HIGH (Provider resilience and user choice)

## Context

Phase 39 made WebSearch real with Tavily. That is enough for a working default, but it leaves the
feature dependent on one provider, one account system, and one quota/pricing model.

The highest-ROI standalone gap for Phase 58 is adding secondary WebSearch providers behind the
existing provider abstraction.

## Goal

Add at least one additional provider, preferably two:

1. `brave` for a reliable hosted search API with an independent index.
2. `searxng` for a no-vendor/self-hosted option.

Keep Tavily as the default recommended provider.

## Scope

In scope:

- provider implementations
- provider-specific config
- mocked tests
- optional live checks through `--doctor --doctor-live`
- README docs
- security docs if needed

Out of scope:

- scraping Google/DuckDuckGo directly
- browser automation
- paid-provider-specific advanced features
- ranking/merging results across multiple providers
- making live provider tests mandatory in CI

## Provider Config

Existing:

```bash
D2C_WEBSEARCH_PROVIDER=tavily
D2C_WEBSEARCH_API_KEY=...
```

Add Brave:

```bash
D2C_WEBSEARCH_PROVIDER=brave
D2C_WEBSEARCH_API_KEY=...
```

Add SearXNG:

```bash
D2C_WEBSEARCH_PROVIDER=searxng
D2C_WEBSEARCH_BASE_URL=http://localhost:8080
```

SearXNG should not require an API key by default.

## Brave Provider

Endpoint:

```text
https://api.search.brave.com/res/v1/web/search
```

Auth:

```text
X-Subscription-Token: <key>
```

Map Brave results into the existing normalized result shape:

```text
title
url
snippet
source
published_at if available
```

Handle:

- 401/403 auth errors
- 429 rate limits
- timeout
- malformed response
- empty results

## SearXNG Provider

Endpoint:

```text
GET <base_url>/search?q=<query>&format=json
```

Map:

```text
title -> title
url -> url
content -> snippet
engine -> source, if present
```

Handle:

- missing base URL
- HTTP errors
- instances with JSON disabled
- malformed response
- empty results

## Tests

Add tests for:

1. provider registry contains `tavily`, `brave`, and `searxng`
2. Brave request uses `X-Subscription-Token`
3. Brave auth/rate-limit/timeout errors map cleanly
4. Brave results normalize correctly
5. SearXNG request sends `format=json`
6. SearXNG does not require an API key
7. SearXNG JSON-disabled/malformed response maps to clean error
8. domain and recency filters degrade clearly when a provider cannot support them
9. no provider output leaks API keys
10. doctor reports provider-specific config accurately

Use mocked HTTP clients. Live checks stay optional.

## Docs

Update:

- `README.md`
- `docs/security.md` if provider risks need clarification
- `web-resources.md` only if new provider docs are linked

Document tradeoffs:

```text
Tavily: easiest agent-oriented hosted provider
Brave: hosted provider with independent index
SearXNG: self-hosted/no-vendor option, reliability depends on instance
```

## Verification

Run:

```bash
pytest tests/test_web_search.py
pytest tests/test_doctor.py
pytest
ruff check .
ruff format --check .
mypy src/d2c
bandit -c pyproject.toml -r src/d2c
pip-audit
python -m build
twine check dist/*
```

Optional live checks:

```bash
D2C_WEBSEARCH_PROVIDER=brave D2C_WEBSEARCH_API_KEY=... python -m d2c --doctor --doctor-live
D2C_WEBSEARCH_PROVIDER=searxng D2C_WEBSEARCH_BASE_URL=http://localhost:8080 python -m d2c --doctor --doctor-live
```

## Acceptance Criteria

- WebSearch supports at least one provider besides Tavily.
- Provider errors are clean and do not leak secrets.
- SearXNG works without an API key when a valid instance is configured.
- Tests use mocked network and pass in CI.
- README documents provider setup and tradeoffs.
- Full gate suite remains green.

