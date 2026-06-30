"""WebFetch — fetch a URL and return content. Paper: read-only external access.

The model provides the URL; the tool fetches, converts HTML to markdown,
and returns truncated content. No internal URL guessing — model must
provide a complete, valid URL.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar
from urllib.parse import urlparse

from d2c.tools import PermissionCategory, Tool, ToolResult


WEB_FETCH_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "url": {
            "type": "string",
            "description": "The URL to fetch content from. Must be a fully-formed valid URL.",
        },
        "prompt": {
            "type": "string",
            "description": "A prompt describing what information to extract from the page.",
        },
        "max_length": {
            "type": "integer",
            "description": "Maximum characters to return (default: 10000).",
        },
    },
    "required": ["url"],
}


# Simple HTML tag stripping for when no markdown converter is available
_STRIP_SCRIPT_STYLE = re.compile(
    r'<(script|style|noscript|iframe|object|embed|form)[^>]*>.*?</\1>',
    re.DOTALL | re.IGNORECASE,
)
_STRIP_TAGS = re.compile(r'<[^>]+>')
_STRIP_COMMENTS = re.compile(r'<!--.*?-->', re.DOTALL)
_NORMALIZE_WS = re.compile(r'\s+')


def _html_to_text(html: str) -> str:
    """Basic HTML-to-text conversion. Strips tags, scripts, styles."""
    text = _STRIP_COMMENTS.sub('', html)
    text = _STRIP_SCRIPT_STYLE.sub('', text)
    text = _STRIP_TAGS.sub(' ', text)
    text = _NORMALIZE_WS.sub(' ', text)
    return text.strip()


class WebFetchTool(Tool):
    """Fetch content from a URL and return as text/markdown.

    Paper: WebFetch is a read-only tool for external information access.
    The model provides the URL — no internal guessing.
    """

    name: ClassVar[str] = "WebFetch"
    description: ClassVar[str] = (
        "Fetches content from a specified URL and processes it. "
        "Takes a URL and a prompt as input, fetches the URL content, "
        "converts HTML to text, and returns the content. "
        "Use this tool when you need to retrieve and analyze web content. "
        "IMPORTANT: Do NOT guess or construct URLs — only fetch URLs "
        "explicitly provided by the user or found in code."
    )
    input_schema: ClassVar[dict[str, Any]] = WEB_FETCH_INPUT_SCHEMA
    category: ClassVar[PermissionCategory] = PermissionCategory.READ
    is_concurrent_safe: ClassVar[bool] = True

    async def execute(
        self,
        url: str = "",
        prompt: str = "",
        max_length: int = 10_000,
        **kwargs: Any,
    ) -> ToolResult:
        if not url:
            return ToolResult(output="Error: URL is required.", error=True)

        # Validate URL
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return ToolResult(
                output=f"Error: Invalid URL: {url}. Must be a fully-formed URL with scheme and host.",
                error=True,
            )

        if parsed.scheme not in ("http", "https"):
            return ToolResult(
                output=f"Error: Unsupported URL scheme: {parsed.scheme}. Only http and https are supported.",
                error=True,
            )

        try:
            import httpx
        except ImportError:
            return ToolResult(
                output="Error: httpx is required for WebFetch. Install with: pip install httpx",
                error=True,
            )

        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                response = await client.get(
                    url,
                    headers={
                        "User-Agent": "d2c/1.0 (CLI coding agent)",
                        "Accept": "text/html,text/plain,*/*",
                    },
                )
                response.raise_for_status()

                content_type = response.headers.get("content-type", "")
                if "text/html" in content_type:
                    text = _html_to_text(response.text)
                else:
                    text = response.text

                if len(text) > max_length:
                    text = text[:max_length]
                    text += f"\n\n[Truncated to {max_length} chars. Original size: {len(response.text)}]"

                return ToolResult(
                    output=text,
                    metadata={
                        "url": url,
                        "status_code": response.status_code,
                        "content_type": content_type,
                        "original_size": len(response.text),
                    },
                )
        except httpx.HTTPStatusError as e:
            return ToolResult(
                output=f"Error fetching URL: HTTP {e.response.status_code}",
                error=True,
                metadata={"status_code": e.response.status_code},
            )
        except httpx.TimeoutException:
            return ToolResult(output=f"Error: Request to {url} timed out after 30 seconds.", error=True)
        except httpx.RequestError as e:
            return ToolResult(output=f"Error fetching URL: {e}", error=True)
        except Exception as e:
            return ToolResult(output=f"Error processing URL content: {e}", error=True)
