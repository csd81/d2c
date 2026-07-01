"""Phase 39: real WebSearch backend (provider-neutral, mocked network)."""

import pytest

import d2c.tools.web_search as ws
from d2c.tools.web_search import (
    SearchResult,
    TavilyProvider,
    WebSearchAuthError,
    WebSearchRateLimitError,
    WebSearchTimeoutError,
    WebSearchTool,
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for var in ("D2C_WEBSEARCH_PROVIDER", "D2C_WEBSEARCH_API_KEY", "D2C_WEBSEARCH_TIMEOUT"):
        monkeypatch.delenv(var, raising=False)
    yield


def _configure(monkeypatch, provider="tavily", key="sk-test-secret"):
    monkeypatch.setenv("D2C_WEBSEARCH_PROVIDER", provider)
    monkeypatch.setenv("D2C_WEBSEARCH_API_KEY", key)


class FakeProvider:
    """Records call kwargs and returns/raises a scripted value."""

    def __init__(self, results=None, raises=None):
        self._results = results or []
        self._raises = raises
        self.calls = []

    async def search(self, query, *, max_results, recency_days=None, domains=None):
        self.calls.append(
            {
                "query": query,
                "max_results": max_results,
                "recency_days": recency_days,
                "domains": domains,
            }
        )
        if self._raises:
            raise self._raises
        return self._results


def _inject(monkeypatch, fake):
    monkeypatch.setattr(ws, "_make_provider", lambda name, key, timeout: fake)


# ── Tool: config + validation ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_unconfigured_returns_clear_error(monkeypatch):
    res = await WebSearchTool().execute(query="anything")
    assert res.error is True
    assert "not configured" in res.output.lower()
    assert res.metadata.get("configured") is False


@pytest.mark.asyncio
async def test_empty_query_rejected(monkeypatch):
    _configure(monkeypatch)
    res = await WebSearchTool().execute(query="   ")
    assert res.error is True
    assert "query is required" in res.output.lower()


@pytest.mark.asyncio
async def test_unsupported_provider(monkeypatch):
    monkeypatch.setenv("D2C_WEBSEARCH_PROVIDER", "bing")
    monkeypatch.setenv("D2C_WEBSEARCH_API_KEY", "k")
    res = await WebSearchTool().execute(query="x")
    assert res.error is True
    assert "unsupported" in res.output.lower()


@pytest.mark.asyncio
async def test_max_results_clamped(monkeypatch):
    _configure(monkeypatch)
    fake = FakeProvider(results=[])
    _inject(monkeypatch, fake)
    await WebSearchTool().execute(query="x", max_results=50)
    assert fake.calls[0]["max_results"] == 10
    await WebSearchTool().execute(query="x", max_results=0)
    assert fake.calls[1]["max_results"] == 1


@pytest.mark.asyncio
async def test_filters_forwarded(monkeypatch):
    _configure(monkeypatch)
    fake = FakeProvider(results=[])
    _inject(monkeypatch, fake)
    await WebSearchTool().execute(query="x", recency_days=7, domains=["python.org"])
    assert fake.calls[0]["recency_days"] == 7
    assert fake.calls[0]["domains"] == ["python.org"]


# ── Tool: results + errors ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_successful_search_formats_results(monkeypatch):
    _configure(monkeypatch)
    fake = FakeProvider(
        results=[
            SearchResult(title="Py 3.13", url="https://python.org/3.13", snippet="release notes"),
            SearchResult(
                title="Changelog", url="https://docs.python.org/cl", snippet="what changed"
            ),
        ]
    )
    _inject(monkeypatch, fake)
    res = await WebSearchTool().execute(query="python 3.13")
    assert res.error is False
    assert "Py 3.13" in res.output
    assert "https://python.org/3.13" in res.output
    assert "release notes" in res.output
    assert res.metadata["result_count"] == 2
    assert res.metadata["provider"] == "tavily"
    assert len(res.metadata["results"]) == 2


@pytest.mark.asyncio
async def test_empty_results_is_not_an_error(monkeypatch):
    _configure(monkeypatch)
    _inject(monkeypatch, FakeProvider(results=[]))
    res = await WebSearchTool().execute(query="obscure")
    assert res.error is False
    assert "no results" in res.output.lower()
    assert res.metadata["result_count"] == 0


@pytest.mark.asyncio
async def test_timeout_clean_error(monkeypatch):
    _configure(monkeypatch)
    _inject(monkeypatch, FakeProvider(raises=WebSearchTimeoutError("timed out")))
    res = await WebSearchTool().execute(query="x")
    assert res.error is True
    assert "timed out" in res.output.lower()


@pytest.mark.asyncio
async def test_auth_error_does_not_leak_key(monkeypatch):
    _configure(monkeypatch, key="sk-super-secret")
    _inject(monkeypatch, FakeProvider(raises=WebSearchAuthError("rejected")))
    res = await WebSearchTool().execute(query="x")
    assert res.error is True
    assert "sk-super-secret" not in res.output
    assert "authentication" in res.output.lower()


@pytest.mark.asyncio
async def test_rate_limit_error(monkeypatch):
    _configure(monkeypatch)
    _inject(monkeypatch, FakeProvider(raises=WebSearchRateLimitError("429")))
    res = await WebSearchTool().execute(query="x")
    assert res.error is True
    assert "rate limit" in res.output.lower()


# ── Provider: normalization + HTTP status mapping (mock _http_post_json) ─


@pytest.mark.asyncio
async def test_tavily_normalizes_response(monkeypatch):
    async def fake_post(url, payload, timeout):
        assert payload["query"] == "hello"
        assert payload["max_results"] == 3
        return 200, {
            "results": [
                {
                    "title": "A",
                    "url": "https://a.com",
                    "content": "  snip a  ",
                    "published_date": "2026-06-01",
                },
                {"title": "B", "url": "https://b.com", "content": "snip b"},
            ]
        }

    monkeypatch.setattr(ws, "_http_post_json", fake_post)
    out = await TavilyProvider("k").search("hello", max_results=3)
    assert [r.title for r in out] == ["A", "B"]
    assert out[0].snippet == "snip a"  # trimmed
    assert out[0].published_at == "2026-06-01"
    assert out[0].source == "tavily"


@pytest.mark.asyncio
async def test_tavily_passes_filters(monkeypatch):
    captured = {}

    async def fake_post(url, payload, timeout):
        captured.update(payload)
        return 200, {"results": []}

    monkeypatch.setattr(ws, "_http_post_json", fake_post)
    await TavilyProvider("k").search("q", max_results=5, recency_days=3, domains=["x.com"])
    assert captured["days"] == 3
    assert captured["include_domains"] == ["x.com"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,exc",
    [
        (401, WebSearchAuthError),
        (403, WebSearchAuthError),
        (429, WebSearchRateLimitError),
    ],
)
async def test_tavily_status_mapping(monkeypatch, status, exc):
    async def fake_post(url, payload, timeout):
        return status, {}

    monkeypatch.setattr(ws, "_http_post_json", fake_post)
    with pytest.raises(exc):
        await TavilyProvider("k").search("q", max_results=5)
