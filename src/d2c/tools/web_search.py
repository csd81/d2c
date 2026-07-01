"""WebSearch — real web search via a pluggable provider. Paper: read-only
external access.

Provider + API key are read from the environment (loaded from .env by
Config.load):

    D2C_WEBSEARCH_PROVIDER   e.g. "tavily"
    D2C_WEBSEARCH_API_KEY    provider API key
    D2C_WEBSEARCH_TIMEOUT    optional request timeout in seconds (default 15)

When unconfigured, returns a clear (non-secret) error rather than faking
results. Only the explicit query and optional filters are sent to the
provider — never project file contents.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any, ClassVar, Protocol

from d2c.tools import PermissionCategory, Tool, ToolResult

TAVILY_URL = "https://api.tavily.com/search"
DEFAULT_TIMEOUT = 15.0
UNCONFIGURED_MESSAGE = (
    "WebSearch is not configured. Set D2C_WEBSEARCH_PROVIDER (e.g. 'tavily') "
    "and D2C_WEBSEARCH_API_KEY to enable web search."
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


# ── HTTP helper (isolated so tests can mock it without httpx) ─────────


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


# ── Tavily provider ───────────────────────────────────────────────────


class TavilyProvider:
    name = "tavily"

    def __init__(self, api_key: str, timeout: float = DEFAULT_TIMEOUT):
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


_PROVIDERS: dict[str, type] = {"tavily": TavilyProvider}


def _make_provider(name: str, api_key: str, timeout: float) -> SearchProvider | None:
    cls = _PROVIDERS.get(name)
    if cls is None:
        return None
    return cls(api_key, timeout)


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
    """Search the web via a configured provider (Tavily). Returns titles,
    URLs, and snippets. Read-only; only the query and optional filters leave
    the machine."""

    name: ClassVar[str] = "WebSearch"
    description: ClassVar[str] = (
        "Search the web for up-to-date information. Returns titles, URLs, and "
        "snippets. Requires D2C_WEBSEARCH_PROVIDER and D2C_WEBSEARCH_API_KEY."
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
        try:
            timeout = float(os.environ.get("D2C_WEBSEARCH_TIMEOUT", "") or DEFAULT_TIMEOUT)
        except ValueError:
            timeout = DEFAULT_TIMEOUT

        if not provider_name and not api_key:
            return ToolResult(
                output=UNCONFIGURED_MESSAGE, error=True, metadata={"configured": False}
            )
        if not provider_name:
            provider_name = "tavily"

        provider = _make_provider(provider_name, api_key, timeout)
        if provider is None:
            return ToolResult(
                output=f"Error: unsupported WebSearch provider '{provider_name}'. "
                f"Supported: {', '.join(sorted(_PROVIDERS))}.",
                error=True,
                metadata={"configured": False},
            )
        if not api_key:
            return ToolResult(
                output=UNCONFIGURED_MESSAGE, error=True, metadata={"configured": False}
            )

        from d2c.observability import audit

        audit(
            "websearch_request",
            provider=provider_name,
            max_results=max_results,
            domain_count=len(domains or []),
            recency_days=recency_days,
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

        if not results:
            return ToolResult(
                output=f"No results found for: {query}",
                metadata={"provider": provider_name, "query": query, "result_count": 0},
            )

        return ToolResult(
            output=_format_results(results),
            metadata={
                "provider": provider_name,
                "query": query,
                "result_count": len(results),
                "results": [asdict(r) for r in results],
            },
        )
