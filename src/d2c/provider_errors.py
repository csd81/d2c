"""Phase 84: DeepSeek/Anthropic-SDK provider error classification.

Maps the provider failures DeepSeek documents (HTTP 400/401/402/403/404/422/
429/500/503/504) plus connection/timeout errors into one concise, actionable
user-facing message, used by both the streaming and non-streaming model-call
paths in ``loop.py``.

Safety: only a status code and a short, redacted provider message are surfaced.
It never stringifies the whole exception/response, and never includes API keys,
request bodies, prompts, or tool inputs.
"""

from __future__ import annotations

from dataclasses import dataclass

_MAX_DETAIL = 200  # cap the provider-message snippet


@dataclass(frozen=True)
class ProviderErrorInfo:
    status_code: int | None
    kind: str
    message: str  # user-facing, safe to print
    retryable: bool


# status -> (kind, message, retryable). Wording follows the DeepSeek docs.
_STATUS_MAP: dict[int, tuple[str, str, bool]] = {
    400: ("bad_request", "DeepSeek rejected the request (400). Check the request format.", False),
    401: (
        "auth",
        "DeepSeek authentication failed (401). Check DEEPSEEK_API_KEY.",
        False,
    ),
    402: (
        "insufficient_balance",
        "DeepSeek balance is insufficient (402). Add credits or switch accounts.",
        False,
    ),
    403: (
        "permission",
        "DeepSeek denied access (403). Check your account permissions.",
        False,
    ),
    404: (
        "not_found",
        "DeepSeek could not find the resource (404). Check the model name.",
        False,
    ),
    422: (
        "invalid_params",
        "DeepSeek rejected the request (422). "
        "Check model, thinking, max_tokens, and tool parameters.",
        False,
    ),
    429: (
        "rate_limit",
        "DeepSeek is rate-limiting or traffic-controlling this request (429). Retry shortly.",
        True,
    ),
    500: ("server_error", "DeepSeek had a server error (500). Retry shortly.", True),
    503: (
        "unavailable",
        "DeepSeek is temporarily unavailable or overloaded (503). Retry shortly.",
        True,
    ),
    504: (
        "timeout",
        "DeepSeek timed out (504). Retry, reduce prompt/output size, or use a smaller request.",
        True,
    ),
}


def _status_code(exc: BaseException) -> int | None:
    code = getattr(exc, "status_code", None)
    if isinstance(code, int):
        return code
    resp = getattr(exc, "response", None)
    code = getattr(resp, "status_code", None)
    return code if isinstance(code, int) else None


def _safe_detail(exc: BaseException) -> str:
    """A short, redacted provider message — never the whole request/response."""
    from d2c.observability import redact

    msg = getattr(exc, "message", None)
    if not isinstance(msg, str) or not msg.strip():
        msg = str(exc)
    msg = str(redact(msg)).strip().replace("\n", " ")
    return msg[:_MAX_DETAIL]


def _is_connection_error(exc: BaseException) -> bool:
    """A network/connection failure (no HTTP status), kept distinct from
    HTTP status errors so it isn't mislabeled as a provider-side 5xx."""
    try:
        import anthropic

        if isinstance(exc, anthropic.APIConnectionError):
            return True
    except Exception:
        pass
    name = type(exc).__name__
    return _status_code(exc) is None and ("Connection" in name or "Timeout" in name)


def classify_provider_error(exc: BaseException) -> ProviderErrorInfo:
    """Classify a provider exception into a ProviderErrorInfo."""
    if _is_connection_error(exc):
        return ProviderErrorInfo(
            None,
            "connection",
            "Could not reach DeepSeek (network/connection error). Check your connection and retry.",
            True,
        )

    status = _status_code(exc)
    if status in _STATUS_MAP:
        kind, message, retryable = _STATUS_MAP[status]
        return ProviderErrorInfo(status, kind, message, retryable)

    if status is not None:
        detail = _safe_detail(exc)
        retryable = 500 <= status < 600
        base = f"DeepSeek returned an error ({status})."
        return ProviderErrorInfo(status, "http_error", f"{base} {detail}".strip(), retryable)

    return ProviderErrorInfo(None, "unknown", f"Error calling DeepSeek: {_safe_detail(exc)}", False)


def format_provider_error(exc: BaseException) -> str:
    """The concise, safe, user-facing message for a provider exception."""
    return classify_provider_error(exc).message
