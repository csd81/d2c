# Phase 39: Real WebSearch backend

**Priority:** HIGH (Agent usefulness after safety hardening)

## Context

`WebSearch` exists as a built-in tool but currently returns a "not configured" style stub response.
That means the agent advertises a capability that cannot actually retrieve current information.

After Phase 38 hardens shell permissions, the next highest-ROI feature is to make WebSearch real in
a controlled, testable way.

## Goal

Implement a real WebSearch backend with:

1. A provider abstraction.
2. At least one working provider.
3. Search result normalization.
4. Clear error behavior when unconfigured.
5. Tests with mocked network calls.
6. Result formatting that gives the model usable titles, snippets, and URLs.

## Scope

In scope:

- built-in `WebSearch` tool behavior
- provider config through env/config
- HTTP client implementation
- result normalization
- timeout/retry/error handling
- tests without live network access
- README/COMPARISON updates after verification

Out of scope:

- browser rendering
- page extraction/full WebFetch rewrite
- ranking model
- paid-provider-specific advanced operators beyond the first provider
- automatic browsing outside explicit tool use

## Files to Inspect/Modify

1. `src/d2c/tools/web_search.py`
   - Replace stub with provider-backed implementation.
   - Keep the tool API stable if possible.

2. `src/d2c/config.py`
   - Add provider configuration.
   - Load env vars such as provider name, API key, endpoint, and timeout.

3. `src/d2c/tools/pool.py`
   - Ensure configured WebSearch is assembled with the active config if needed.

4. `tests/test_web_search.py`
   - Add mocked-provider tests.

5. `README.md`
   - Document setup.

6. `COMPARISON.md`
   - Move WebSearch from unresolved stub to implemented only after tests pass.

## Provider Strategy

Use a small provider interface:

```python
class SearchProvider(Protocol):
    async def search(
        self,
        query: str,
        *,
        max_results: int,
        recency_days: int | None = None,
        domains: list[str] | None = None,
    ) -> list[SearchResult]:
        ...
```

Normalize all providers into:

```python
@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    source: str | None = None
    published_at: str | None = None
```

This keeps the tool stable if providers change later.

## First Provider

Pick one initial provider based on lowest implementation friction.

Recommended options:

- **Tavily**: simple search API and commonly used for agent search.
- **Brave Search API**: good general web search, requires API key.
- **SerpAPI**: broad support, requires API key.

Recommended default:

```text
D2C_WEBSEARCH_PROVIDER=tavily
D2C_WEBSEARCH_API_KEY=...
```

If no provider/key is configured, return a clear tool result:

```text
WebSearch is not configured. Set D2C_WEBSEARCH_PROVIDER and D2C_WEBSEARCH_API_KEY.
```

Do not silently fake results.

## Tool Input

Keep the input simple:

```json
{
  "query": "string",
  "max_results": 5,
  "recency_days": null,
  "domains": []
}
```

Validation:

- `query` required and non-empty.
- `max_results` clamped, for example `1..10`.
- `recency_days` optional positive integer.
- `domains` optional list of domain strings.

## Result Formatting

Return compact markdown-like output:

```text
1. Title
   URL: https://example.com/page
   Snippet: ...

2. Title
   URL: https://example.org/page
   Snippet: ...
```

Also include structured metadata if the local `ToolResult` supports it:

```python
metadata={
    "provider": provider_name,
    "query": query,
    "result_count": len(results),
    "results": [asdict(r) for r in results],
}
```

## Error Handling

Handle:

- missing provider config
- missing API key
- unsupported provider
- HTTP timeout
- HTTP 401/403
- HTTP 429
- malformed provider response
- empty result set

Errors should be tool errors only when the search itself failed. "No results" should be a normal
non-error result.

## Safety and Privacy

- Do not include API keys in output, logs, metadata, or exceptions.
- Apply a short timeout by default.
- Avoid sending project file contents to search. The tool should only send the explicit query and
  optional filters.
- Keep domain filters explicit; do not infer private domains from local files.

## Tests

Add tests for:

1. Missing config returns a clear unconfigured message.
2. Empty query is rejected.
3. `max_results` is clamped.
4. Provider response is normalized into `SearchResult`.
5. Successful search returns formatted titles, snippets, and URLs.
6. HTTP timeout returns a clean error.
7. HTTP 401/403 returns an auth/config error without exposing the key.
8. HTTP 429 returns a rate-limit error.
9. Empty provider results return "No results".
10. Domain and recency filters are passed to the provider request.

Use mocked HTTP clients; do not require live network in tests.

## Verification

Run:

```bash
pytest tests/test_web_search.py
pytest
```

Manual optional check with a real key:

```bash
D2C_WEBSEARCH_PROVIDER=tavily \
D2C_WEBSEARCH_API_KEY=... \
python -m d2c "Search the web for the latest Python 3.13 release notes and summarize with links"
```

## Acceptance Criteria

- `WebSearch` no longer returns a stub when configured.
- Missing configuration produces a useful, non-secret error.
- Search results include titles, snippets, and URLs.
- Provider errors are handled cleanly.
- Tests pass without live network access.
- `README.md` documents configuration.
- `COMPARISON.md` accurately marks WebSearch as implemented only after verification.

## Expected Outcome

The agent gains real current-information retrieval while keeping the implementation provider-neutral
and testable. This expands capability after the shell safety boundary has been hardened.
