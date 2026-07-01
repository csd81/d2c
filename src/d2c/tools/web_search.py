"""WebSearch — real web search via a pluggable provider. Paper: read-only
external access.

Provider config is read from the environment (loaded from .env by
Config.load):

    D2C_WEBSEARCH_PROVIDER   "tavily" (default), "brave", or "searxng"
    D2C_WEBSEARCH_API_KEY    provider API key (tavily/brave; not needed by searxng)
    D2C_WEBSEARCH_BASE_URL   self-hosted instance URL (searxng only)
    D2C_WEBSEARCH_TIMEOUT    optional request timeout in seconds (default 15)

When unconfigured, returns a clear (non-secret) error rather than faking
results. Only the explicit query and optional filters are sent to the
provider — never project file contents. Not every provider supports every
filter (recency_days/domains); unsupported filters are dropped, never
silently misapplied, and noted in the result metadata.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any, ClassVar, Protocol

from d2c.tools import PermissionCategory, Tool, ToolResult

TAVILY_URL = "https://api.tavily.com/search"
BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
DEFAULT_TIMEOUT = 15.0
UNCONFIGURED_MESSAGE = (
    "WebSearch is not configured. Set D2C_WEBSEARCH_PROVIDER (e.g. 'tavily', "
    "'brave', or 'searxng') and D2C_WEBSEARCH_API_KEY (searxng: "
    "D2C_WEBSEARCH_BASE_URL instead) to enable web search."
)


# ── Errors ────────────────────────────────────────────────────────────


class WebSearchError(Exception):
    """A search request failed. Message never contains the API key."""


class WebSearchAuthError(WebSearchError):
    pass


class WebSearchRateLimitError(WebSearchError):
    pass


class WebSearchTimeoutError(WebSearchError):
    pass


# ── Normalized result ─────────────────────────────────────────────────


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    source: str | None = None
    published_at: str | None = None


class SearchProvider(Protocol):
    async def search(
        self,
        query: str,
        *,
        max_results: int,
        recency_days: int | None = None,
        domains: list[str] | None = None,
    ) -> list[SearchResult]: ...


# ── HTTP helpers (isolated so tests can mock them without httpx) ──────


async def _http_post_json(url: str, payload: dict, timeout: float) -> tuple[int, dict]:
    """POST JSON and return (status_code, parsed_json). Translates transport
    errors into WebSearch* exceptions."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload)
    except httpx.TimeoutException as e:
        raise WebSearchTimeoutError("search request timed out") from e
    except httpx.HTTPError as e:
        raise WebSearchError(f"HTTP transport error: {type(e).__name__}") from e

    try:
        data = resp.json()
    except Exception:
        data = {}
    return resp.status_code, data


async def _http_get_json(url: str, params: dict, headers: dict, timeout: float) -> tuple[int, dict]:
    """GET and return (status_code, parsed_json). Translates transport
    errors into WebSearch* exceptions. A non-JSON 200 response (e.g. an
    HTML page from a misconfigured instance) parses to {} rather than
    raising, so callers can distinguish "empty result set" from "didn't
    parse" via the returned dict."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, params=params, headers=headers)
    except httpx.TimeoutException as e:
        raise WebSearchTimeoutError("search request timed out") from e
    except httpx.HTTPError as e:
        raise WebSearchError(f"HTTP transport error: {type(e).__name__}") from e

    try:
        data = resp.json()
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    return resp.status_code, data


# ── Tavily provider ───────────────────────────────────────────────────


class TavilyProvider:
    name = "tavily"
    requires_api_key = True
    requires_base_url = False
    supports_recency = True
    supports_domains = True

    def __init__(self, api_key: str, timeout: float = DEFAULT_TIMEOUT, base_url: str = ""):
        self._api_key = api_key
        self._timeout = timeout

    async def search(
        self,
        query: str,
        *,
        max_results: int,
        recency_days: int | None = None,
        domains: list[str] | None = None,
    ) -> list[SearchResult]:
        payload: dict[str, Any] = {
            "api_key": self._api_key,
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
        }
        if recency_days:
            payload["days"] = int(recency_days)
        if domains:
            payload["include_domains"] = list(domains)

        status, data = await _http_post_json(TAVILY_URL, payload, self._timeout)

        if status in (401, 403):
            raise WebSearchAuthError("search provider rejected the API key")
        if status == 429:
            raise WebSearchRateLimitError("search provider rate limit exceeded")
        if status >= 400:
            raise WebSearchError(f"search provider error (HTTP {status})")

        results: list[SearchResult] = []
        for item in (data.get("results") or [])[:max_results]:
            results.append(
                SearchResult(
                    title=item.get("title") or "(untitled)",
                    url=item.get("url") or "",
                    snippet=(item.get("content") or "").strip(),
                    source="tavily",
                    published_at=item.get("published_date"),
                )
            )
        return results


# ── Brave provider ───────────────────────────────────────────────────


class BraveProvider:
    """Brave Search API: hosted, independent index, header-based auth."""

    name = "brave"
    requires_api_key = True
    requires_base_url = False
    # Brave's web-search endpoint has no include-domains/freshness-window
    # equivalent to Tavily's; degrade cleanly rather than fake support.
    supports_recency = False
    supports_domains = False

    def __init__(self, api_key: str, timeout: float = DEFAULT_TIMEOUT, base_url: str = ""):
        self._api_key = api_key
        self._timeout = timeout

    async def search(
        self,
        query: str,
        *,
        max_results: int,
        recency_days: int | None = None,
        domains: list[str] | None = None,
    ) -> list[SearchResult]:
        status, data = await _http_get_json(
            BRAVE_URL,
            params={"q": query, "count": max_results},
            headers={
                "X-Subscription-Token": self._api_key,
                "Accept": "application/json",
            },
            timeout=self._timeout,
        )

        if status in (401, 403):
            raise WebSearchAuthError("search provider rejected the API key")
        if status == 429:
            raise WebSearchRateLimitError("search provider rate limit exceeded")
        if status >= 400:
            raise WebSearchError(f"search provider error (HTTP {status})")

        try:
            web = data.get("web") or {}
            items = (web.get("results") or [])[:max_results]
        except (AttributeError, TypeError) as e:
            raise WebSearchError("malformed response from search provider") from e

        results: list[SearchResult] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            results.append(
                SearchResult(
                    title=item.get("title") or "(untitled)",
                    url=item.get("url") or "",
                    snippet=(item.get("description") or "").strip(),
                    source="brave",
                    published_at=item.get("page_age"),
                )
            )
        return results


# ── SearXNG provider ─────────────────────────────────────────────────


class SearXNGProvider:
    """Self-hosted/no-vendor metasearch via a SearXNG instance's JSON API.

    No API key required. Reliability depends on the configured instance
    (JSON output must be enabled in its settings.yml).
    """

    name = "searxng"
    requires_api_key = False
    requires_base_url = True
    supports_recency = False
    supports_domains = False

    def __init__(self, api_key: str, timeout: float = DEFAULT_TIMEOUT, base_url: str = ""):
        self._timeout = timeout
        self._base_url = base_url.rstrip("/")

    async def search(
        self,
        query: str,
        *,
        max_results: int,
        recency_days: int | None = None,
        domains: list[str] | None = None,
    ) -> list[SearchResult]:
        if not self._base_url:
            raise WebSearchError("SearXNG base URL is not configured")

        status, data = await _http_get_json(
            f"{self._base_url}/search",
            params={"q": query, "format": "json"},
            headers={"Accept": "application/json"},
            timeout=self._timeout,
        )

        if status == 429:
            raise WebSearchRateLimitError("search provider rate limit exceeded")
        if status >= 400:
            raise WebSearchError(
                f"SearXNG instance returned HTTP {status} "
                "(is JSON output enabled in its settings.yml?)"
            )
        if not data:
            # Either a valid-but-empty response, or a 200 that wasn't JSON
            # (common when an instance has the JSON format disabled) — both
            # parse to {} in _http_get_json, so treat as "no results" rather
            # than raising: an instance-config issue still surfaces clearly
            # via the "no results found" message plus provider=searxng.
            return []

        try:
            items = (data.get("results") or [])[:max_results]
        except (AttributeError, TypeError) as e:
            raise WebSearchError("malformed response from SearXNG instance") from e

        results: list[SearchResult] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            results.append(
                SearchResult(
                    title=item.get("title") or "(untitled)",
                    url=item.get("url") or "",
                    snippet=(item.get("content") or "").strip(),
                    source=item.get("engine") or "searxng",
                    published_at=item.get("publishedDate"),
                )
            )
        return results


# Capability flags (requires_api_key/requires_base_url/supports_recency/
# supports_domains) live on each concrete provider class below, read via
# getattr() in WebSearchTool.execute() rather than declared on the
# Protocol (Protocol + type[] dict-value typing don't mix cleanly).
_PROVIDERS: dict[str, type[Any]] = {
    "tavily": TavilyProvider,
    "brave": BraveProvider,
    "searxng": SearXNGProvider,
}


def _make_provider(
    name: str, api_key: str, timeout: float, base_url: str = ""
) -> SearchProvider | None:
    cls = _PROVIDERS.get(name)
    if cls is None:
        return None
    return cls(api_key, timeout, base_url)


def _format_results(results: list[SearchResult]) -> str:
    blocks = []
    for i, r in enumerate(results, 1):
        blocks.append(f"{i}. {r.title}\n   URL: {r.url}\n   Snippet: {r.snippet}")
    return "\n\n".join(blocks)


# ── Tool ──────────────────────────────────────────────────────────────

WEB_SEARCH_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "The search query."},
        "max_results": {
            "type": "integer",
            "description": "Maximum number of results (default 5, clamped 1-10).",
        },
        "recency_days": {
            "type": "integer",
            "description": "Optional: only results from the last N days.",
        },
        "domains": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional: restrict results to these domains.",
        },
    },
    "required": ["query"],
}


class WebSearchTool(Tool):
    """Search the web via a configured provider (tavily/brave/searxng).
    Returns titles, URLs, and snippets. Read-only; only the query and
    optional filters leave the machine."""

    name: ClassVar[str] = "WebSearch"
    description: ClassVar[str] = (
        "Search the web for up-to-date information. Returns titles, URLs, and "
        "snippets. Requires D2C_WEBSEARCH_PROVIDER (tavily, brave, or searxng) "
        "and D2C_WEBSEARCH_API_KEY (searxng: D2C_WEBSEARCH_BASE_URL instead)."
    )
    input_schema: ClassVar[dict[str, Any]] = WEB_SEARCH_INPUT_SCHEMA
    category: ClassVar[PermissionCategory] = PermissionCategory.READ
    is_concurrent_safe: ClassVar[bool] = True

    async def execute(
        self,
        query: str = "",
        max_results: int = 5,
        recency_days: int | None = None,
        domains: list[str] | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        query = (query or "").strip()
        if not query:
            return ToolResult(output="Error: Search query is required.", error=True)

        try:
            max_results = int(max_results)
        except (TypeError, ValueError):
            max_results = 5
        max_results = min(max(max_results, 1), 10)

        provider_name = os.environ.get("D2C_WEBSEARCH_PROVIDER", "").strip().lower()
        api_key = os.environ.get("D2C_WEBSEARCH_API_KEY", "").strip()
        base_url = os.environ.get("D2C_WEBSEARCH_BASE_URL", "").strip()
        try:
            timeout = float(os.environ.get("D2C_WEBSEARCH_TIMEOUT", "") or DEFAULT_TIMEOUT)
        except ValueError:
            timeout = DEFAULT_TIMEOUT

        if not provider_name and not api_key and not base_url:
            return ToolResult(
                output=UNCONFIGURED_MESSAGE, error=True, metadata={"configured": False}
            )
        if not provider_name:
            provider_name = "tavily"

        cls = _PROVIDERS.get(provider_name)
        if cls is None:
            return ToolResult(
                output=f"Error: unsupported WebSearch provider '{provider_name}'. "
                f"Supported: {', '.join(sorted(_PROVIDERS))}.",
                error=True,
                metadata={"configured": False},
            )
        if cls.requires_api_key and not api_key:
            return ToolResult(
                output=f"WebSearch provider '{provider_name}' requires D2C_WEBSEARCH_API_KEY.",
                error=True,
                metadata={"configured": False, "provider": provider_name},
            )
        if cls.requires_base_url and not base_url:
            return ToolResult(
                output=f"WebSearch provider '{provider_name}' requires "
                "D2C_WEBSEARCH_BASE_URL (e.g. http://localhost:8080).",
                error=True,
                metadata={"configured": False, "provider": provider_name},
            )

        provider = _make_provider(provider_name, api_key, timeout, base_url)
        if provider is None:
            # Unreachable in practice: the _PROVIDERS lookup above already
            # validated provider_name. Handled explicitly rather than
            # asserted, so a stripped-assert build can't skip the check.
            return ToolResult(
                output=f"Error: unsupported WebSearch provider '{provider_name}'.",
                error=True,
                metadata={"configured": False},
            )

        unsupported_filters = []
        if recency_days and not cls.supports_recency:
            unsupported_filters.append("recency_days")
        if domains and not cls.supports_domains:
            unsupported_filters.append("domains")

        from d2c.observability import audit

        audit(
            "websearch_request",
            provider=provider_name,
            max_results=max_results,
            domain_count=len(domains or []),
            recency_days=recency_days,
            unsupported_filters=unsupported_filters or None,
        )
        try:
            results = await provider.search(
                query,
                max_results=max_results,
                recency_days=recency_days,
                domains=domains,
            )
        except WebSearchAuthError:
            audit(
                "websearch_error",
                level="ERROR",
                provider=provider_name,
                error_class="WebSearchAuthError",
            )
            return ToolResult(
                output="WebSearch authentication failed (check D2C_WEBSEARCH_API_KEY).",
                error=True,
                metadata={"provider": provider_name},
            )
        except WebSearchRateLimitError:
            audit(
                "websearch_error",
                level="WARNING",
                provider=provider_name,
                error_class="WebSearchRateLimitError",
            )
            return ToolResult(
                output="WebSearch rate limit exceeded; try again later.",
                error=True,
                metadata={"provider": provider_name},
            )
        except WebSearchTimeoutError:
            audit(
                "websearch_error",
                level="WARNING",
                provider=provider_name,
                error_class="WebSearchTimeoutError",
            )
            return ToolResult(
                output="WebSearch timed out.",
                error=True,
                metadata={"provider": provider_name},
            )
        except WebSearchError as e:
            audit(
                "websearch_error",
                level="ERROR",
                provider=provider_name,
                error_class=type(e).__name__,
            )
            return ToolResult(
                output=f"WebSearch failed: {e}",
                error=True,
                metadata={"provider": provider_name},
            )

        audit("websearch_result", provider=provider_name, result_count=len(results))

        note = ""
        if unsupported_filters:
            note = (
                f"(note: {', '.join(unsupported_filters)} not supported by "
                f"'{provider_name}'; ignored)\n\n"
            )

        if not results:
            return ToolResult(
                output=f"{note}No results found for: {query}",
                metadata={
                    "provider": provider_name,
                    "query": query,
                    "result_count": 0,
                    "unsupported_filters": unsupported_filters,
                },
            )

        # Phase 53: delimit retrieved snippets as untrusted data.
        from d2c.untrusted import wrap_untrusted_web

        return ToolResult(
            output=note
            + wrap_untrusted_web(_format_results(results), source=f"web_search:{provider_name}"),
            metadata={
                "provider": provider_name,
                "query": query,
                "result_count": len(results),
                "results": [asdict(r) for r in results],
                "unsupported_filters": unsupported_filters,
            },
        )
