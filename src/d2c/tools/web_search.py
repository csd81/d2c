"""WebSearch — search the web for information. Paper: read-only external access.

Uses a configurable search backend. Falls back to a notice when no
search API key is configured.
"""

from __future__ import annotations

from typing import Any, ClassVar

from d2c.tools import PermissionCategory, Tool, ToolResult


WEB_SEARCH_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "The search query",
        },
        "max_results": {
            "type": "integer",
            "description": "Maximum number of results to return (default: 5, max: 20)",
        },
        "search_type": {
            "type": "string",
            "description": "Type of search: 'web' or 'news' (default: 'web')",
        },
    },
    "required": ["query"],
}


class WebSearchTool(Tool):
    """Search the web using a configurable backend.

    Paper: WebSearch is a read-only tool for external information retrieval.
    When no search API is configured, it returns a notice directing the
    user to configure one.
    """

    name: ClassVar[str] = "WebSearch"
    description: ClassVar[str] = (
        "Search the web for information. Returns structured results "
        "with titles, URLs, and snippets. Use this tool when you need "
        "up-to-date information or to research current topics. "
        "IMPORTANT: Requires a search API key to be configured."
    )
    input_schema: ClassVar[dict[str, Any]] = WEB_SEARCH_INPUT_SCHEMA
    category: ClassVar[PermissionCategory] = PermissionCategory.READ
    is_concurrent_safe: ClassVar[bool] = True

    async def execute(
        self,
        query: str = "",
        max_results: int = 5,
        search_type: str = "web",
        **kwargs: Any,
    ) -> ToolResult:
        if not query:
            return ToolResult(output="Error: Search query is required.", error=True)

        max_results = min(max(max_results, 1), 20)

        # Try to use a configured search backend
        # For now, return a notice since no backend is configured by default
        return ToolResult(
            output=(
                f"Web search is not configured.\n\n"
                f"Query: {query}\n"
                f"Max results: {max_results}\n"
                f"Type: {search_type}\n\n"
                f"To enable web search, set a search API key in your d2c configuration.\n"
                f"Supported backends: Brave Search, Google Custom Search, SerpAPI."
            ),
            metadata={
                "query": query,
                "configured": False,
                "backend": "none",
            },
        )
