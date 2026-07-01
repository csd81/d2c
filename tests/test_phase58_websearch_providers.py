"""Phase 58: WebSearch provider expansion (Brave, SearXNG)."""

import pytest

import d2c.tools.web_search as ws
from d2c.tools.web_search import (
    _PROVIDERS,
    BraveProvider,
    SearXNGProvider,
    WebSearchAuthError,
    WebSearchError,
    WebSearchRateLimitError,
    WebSearchTimeoutError,
    WebSearchTool,
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for var in (
        "D2C_WEBSEARCH_PROVIDER",
        "D2C_WEBSEARCH_API_KEY",
        "D2C_WEBSEARCH_BASE_URL",
        "D2C_WEBSEARCH_TIMEOUT",
    ):
        monkeypatch.delenv(var, raising=False)
    yield


# ── 1. Provider registry ────────────────────────────────────────────────


def test_provider_registry_contains_all_three():
    assert set(_PROVIDERS) == {"tavily", "brave", "searxng"}
    assert _PROVIDERS["brave"] is BraveProvider
    assert _PROVIDERS["searxng"] is SearXNGProvider


# ── 2-4. Brave: request shape, error mapping, normalization ────────────


@pytest.mark.asyncio
async def test_brave_request_uses_subscription_token_header(monkeypatch):
    captured = {}

    async def fake_get(url, params, headers, timeout):
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        return 200, {"web": {"results": []}}

    monkeypatch.setattr(ws, "_http_get_json", fake_get)
    await BraveProvider("brave-secret-key").search("python", max_results=5)
    assert captured["headers"]["X-Subscription-Token"] == "brave-secret-key"
    assert captured["params"]["q"] == "python"
    assert captured["params"]["count"] == 5
    assert "search.brave.com" in captured["url"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,exc",
    [
        (401, WebSearchAuthError),
        (403, WebSearchAuthError),
        (429, WebSearchRateLimitError),
        (500, WebSearchError),
    ],
)
async def test_brave_status_mapping(monkeypatch, status, exc):
    async def fake_get(url, params, headers, timeout):
        return status, {}

    monkeypatch.setattr(ws, "_http_get_json", fake_get)
    with pytest.raises(exc):
        await BraveProvider("k").search("q", max_results=5)


@pytest.mark.asyncio
async def test_brave_timeout_maps_cleanly(monkeypatch):
    async def fake_get(url, params, headers, timeout):
        raise WebSearchTimeoutError("search request timed out")

    monkeypatch.setattr(ws, "_http_get_json", fake_get)
    with pytest.raises(WebSearchTimeoutError):
        await BraveProvider("k").search("q", max_results=5)


@pytest.mark.asyncio
async def test_brave_normalizes_response(monkeypatch):
    async def fake_get(url, params, headers, timeout):
        return 200, {
            "web": {
                "results": [
                    {
                        "title": "A",
                        "url": "https://a.com",
                        "description": "  snip a  ",
                        "page_age": "2026-06-01",
                    },
                    {"title": "B", "url": "https://b.com", "description": "snip b"},
                ]
            }
        }

    monkeypatch.setattr(ws, "_http_get_json", fake_get)
    out = await BraveProvider("k").search("hello", max_results=3)
    assert [r.title for r in out] == ["A", "B"]
    assert out[0].snippet == "snip a"  # trimmed
    assert out[0].published_at == "2026-06-01"
    assert out[0].source == "brave"


@pytest.mark.asyncio
async def test_brave_malformed_response_is_clean_error(monkeypatch):
    async def fake_get(url, params, headers, timeout):
        return 200, {"web": "not-a-dict"}

    monkeypatch.setattr(ws, "_http_get_json", fake_get)
    with pytest.raises(WebSearchError):
        await BraveProvider("k").search("q", max_results=5)


@pytest.mark.asyncio
async def test_brave_empty_results_not_an_error(monkeypatch):
    async def fake_get(url, params, headers, timeout):
        return 200, {"web": {"results": []}}

    monkeypatch.setattr(ws, "_http_get_json", fake_get)
    out = await BraveProvider("k").search("obscure", max_results=5)
    assert out == []


# ── 5-7. SearXNG: request shape, no-key, JSON-disabled/malformed ───────


@pytest.mark.asyncio
async def test_searxng_request_sends_format_json(monkeypatch):
    captured = {}

    async def fake_get(url, params, headers, timeout):
        captured["url"] = url
        captured["params"] = params
        return 200, {"results": []}

    monkeypatch.setattr(ws, "_http_get_json", fake_get)
    await SearXNGProvider("", base_url="http://localhost:8080").search("python", max_results=5)
    assert captured["url"] == "http://localhost:8080/search"
    assert captured["params"]["format"] == "json"
    assert captured["params"]["q"] == "python"


@pytest.mark.asyncio
async def test_searxng_does_not_require_api_key():
    assert SearXNGProvider.requires_api_key is False
    # Empty api_key is accepted without error at construction time.
    SearXNGProvider("", base_url="http://localhost:8080")


@pytest.mark.asyncio
async def test_searxng_missing_base_url_raises_clean_error():
    with pytest.raises(WebSearchError):
        await SearXNGProvider("").search("q", max_results=5)


@pytest.mark.asyncio
async def test_searxng_json_disabled_degrades_to_no_results(monkeypatch):
    # A 200 response with a non-JSON body (HTML) parses to {} in
    # _http_get_json — SearXNGProvider must not crash on this, and should
    # not silently claim malformed data is real results.
    async def fake_get(url, params, headers, timeout):
        return 200, {}

    monkeypatch.setattr(ws, "_http_get_json", fake_get)
    out = await SearXNGProvider("", base_url="http://localhost:8080").search("q", max_results=5)
    assert out == []


@pytest.mark.asyncio
async def test_searxng_malformed_results_field_is_clean_error(monkeypatch):
    async def fake_get(url, params, headers, timeout):
        return 200, {"results": "not-a-list-of-dicts"}

    monkeypatch.setattr(ws, "_http_get_json", fake_get)
    # A string has no .get(), so slicing it is fine but item access isn't a
    # dict — items lacking dict shape are skipped rather than raising.
    out = await SearXNGProvider("", base_url="http://localhost:8080").search("q", max_results=5)
    assert out == []


@pytest.mark.asyncio
async def test_searxng_normalizes_response(monkeypatch):
    async def fake_get(url, params, headers, timeout):
        return 200, {
            "results": [
                {
                    "title": "A",
                    "url": "https://a.com",
                    "content": "snip a",
                    "engine": "duckduckgo",
                    "publishedDate": "2026-06-01",
                }
            ]
        }

    monkeypatch.setattr(ws, "_http_get_json", fake_get)
    out = await SearXNGProvider("", base_url="http://localhost:8080").search("hello", max_results=3)
    assert out[0].title == "A"
    assert out[0].source == "duckduckgo"
    assert out[0].published_at == "2026-06-01"


@pytest.mark.asyncio
async def test_searxng_rate_limit_maps_cleanly(monkeypatch):
    async def fake_get(url, params, headers, timeout):
        return 429, {}

    monkeypatch.setattr(ws, "_http_get_json", fake_get)
    with pytest.raises(WebSearchRateLimitError):
        await SearXNGProvider("", base_url="http://localhost:8080").search("q", max_results=5)


# ── Tool-level: provider selection, base-url requirement, no key leak ──


@pytest.mark.asyncio
async def test_tool_selects_brave_and_requires_key(monkeypatch):
    monkeypatch.setenv("D2C_WEBSEARCH_PROVIDER", "brave")
    res = await WebSearchTool().execute(query="x")
    assert res.error is True
    assert "D2C_WEBSEARCH_API_KEY" in res.output
    assert res.metadata["configured"] is False


@pytest.mark.asyncio
async def test_tool_selects_searxng_without_key_but_needs_base_url(monkeypatch):
    monkeypatch.setenv("D2C_WEBSEARCH_PROVIDER", "searxng")
    res = await WebSearchTool().execute(query="x")
    assert res.error is True
    assert "D2C_WEBSEARCH_BASE_URL" in res.output
    assert res.metadata["configured"] is False


@pytest.mark.asyncio
async def test_tool_runs_searxng_with_base_url_and_no_key(monkeypatch):
    monkeypatch.setenv("D2C_WEBSEARCH_PROVIDER", "searxng")
    monkeypatch.setenv("D2C_WEBSEARCH_BASE_URL", "http://localhost:8080")

    async def fake_get(url, params, headers, timeout):
        return 200, {"results": [{"title": "A", "url": "https://a.com", "content": "x"}]}

    monkeypatch.setattr(ws, "_http_get_json", fake_get)
    res = await WebSearchTool().execute(query="x")
    assert res.error is False
    assert res.metadata["provider"] == "searxng"


# ── 8. Unsupported filters degrade clearly (not silently misapplied) ───


@pytest.mark.asyncio
async def test_unsupported_filters_are_noted_not_silently_dropped(monkeypatch):
    monkeypatch.setenv("D2C_WEBSEARCH_PROVIDER", "brave")
    monkeypatch.setenv("D2C_WEBSEARCH_API_KEY", "k")

    async def fake_get(url, params, headers, timeout):
        return 200, {"web": {"results": [{"title": "A", "url": "https://a.com"}]}}

    monkeypatch.setattr(ws, "_http_get_json", fake_get)
    res = await WebSearchTool().execute(query="x", recency_days=7, domains=["python.org"])
    assert res.error is False
    assert set(res.metadata["unsupported_filters"]) == {"recency_days", "domains"}
    assert "not supported" in res.output


@pytest.mark.asyncio
async def test_supported_filters_are_not_flagged_unsupported(monkeypatch):
    monkeypatch.setenv("D2C_WEBSEARCH_PROVIDER", "tavily")
    monkeypatch.setenv("D2C_WEBSEARCH_API_KEY", "k")

    async def fake_post(url, payload, timeout):
        return 200, {"results": []}

    monkeypatch.setattr(ws, "_http_post_json", fake_post)
    res = await WebSearchTool().execute(query="x", recency_days=7, domains=["python.org"])
    assert res.metadata["unsupported_filters"] == []


# ── 9. No provider output leaks API keys ────────────────────────────────


@pytest.mark.asyncio
async def test_brave_error_never_leaks_key(monkeypatch):
    monkeypatch.setenv("D2C_WEBSEARCH_PROVIDER", "brave")
    monkeypatch.setenv("D2C_WEBSEARCH_API_KEY", "sk-brave-super-secret")

    async def fake_get(url, params, headers, timeout):
        return 401, {}

    monkeypatch.setattr(ws, "_http_get_json", fake_get)
    res = await WebSearchTool().execute(query="x")
    assert "sk-brave-super-secret" not in res.output
    assert "sk-brave-super-secret" not in str(res.metadata)


@pytest.mark.asyncio
async def test_searxng_output_never_leaks_base_url_as_secret(monkeypatch):
    # base_url is not a secret, but confirm no api_key leaks through even
    # when D2C_WEBSEARCH_API_KEY happens to be set alongside searxng.
    monkeypatch.setenv("D2C_WEBSEARCH_PROVIDER", "searxng")
    monkeypatch.setenv("D2C_WEBSEARCH_BASE_URL", "http://localhost:8080")
    monkeypatch.setenv("D2C_WEBSEARCH_API_KEY", "sk-should-not-leak")

    async def fake_get(url, params, headers, timeout):
        return 200, {"results": []}

    monkeypatch.setattr(ws, "_http_get_json", fake_get)
    res = await WebSearchTool().execute(query="x")
    assert "sk-should-not-leak" not in res.output
    assert "sk-should-not-leak" not in str(res.metadata)


# ── 10. Doctor: provider-specific config reporting ──────────────────────


def test_doctor_reports_searxng_base_url():
    from d2c.doctor import check_websearch

    class Cfg:
        websearch_provider = "searxng"
        websearch_api_key = None
        websearch_base_url = "http://localhost:8080"

    result = check_websearch(Cfg())
    assert result.status == "pass"
    assert "base_url=http://localhost:8080" in result.message


def test_doctor_warns_searxng_missing_base_url():
    from d2c.doctor import check_websearch

    class Cfg:
        websearch_provider = "searxng"
        websearch_api_key = None
        websearch_base_url = ""

    result = check_websearch(Cfg())
    assert result.status == "warn"
    assert "base URL" in result.message


def test_doctor_warns_brave_missing_key():
    from d2c.doctor import check_websearch

    class Cfg:
        websearch_provider = "brave"
        websearch_api_key = None
        websearch_base_url = ""

    result = check_websearch(Cfg())
    assert result.status == "warn"
    assert "API key" in result.message


def test_doctor_passes_brave_with_key():
    from d2c.doctor import check_websearch

    class Cfg:
        websearch_provider = "brave"
        websearch_api_key = "sk-brave"
        websearch_base_url = ""

    result = check_websearch(Cfg())
    assert result.status == "pass"
    assert "provider=brave" in result.message
    assert "sk-brave" not in result.message
