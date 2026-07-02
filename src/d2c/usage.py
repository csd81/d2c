"""Usage and cost accounting (Phase 55).

Tracks per-model-call token usage (provider-reported when available,
estimated otherwise) and accumulates session totals. Costs are always
ESTIMATES: provider pricing changes over time; the defaults below are
snapshots. Override or disable via environment:

    D2C_PRICING_INPUT_PER_MILLION       USD per 1M input tokens
    D2C_PRICING_OUTPUT_PER_MILLION      USD per 1M output tokens
    D2C_PRICING_CACHE_READ_PER_MILLION  USD per 1M cache-read tokens
    D2C_DISABLE_COST_ESTIMATES=1        track tokens, skip cost math

Money uses Decimal. Usage recording must never fail the agent loop —
record_model_usage() swallows all errors.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

_MILLION = Decimal(1_000_000)

ENV_INPUT = "D2C_PRICING_INPUT_PER_MILLION"
ENV_OUTPUT = "D2C_PRICING_OUTPUT_PER_MILLION"
ENV_CACHE_READ = "D2C_PRICING_CACHE_READ_PER_MILLION"
ENV_DISABLE = "D2C_DISABLE_COST_ESTIMATES"


# ── Pricing ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ModelPricing:
    """USD per 1M tokens. Cache writes are billed as normal input by
    DeepSeek, so cache_write defaults to the input rate."""

    input_per_million: Decimal
    output_per_million: Decimal
    cache_read_per_million: Decimal
    cache_write_per_million: Decimal


# Snapshot estimates (USD/M tokens); DeepSeek revises pricing — treat as
# defaults to be overridden via D2C_PRICING_*, never as invoice truth.
# NOTE: deepseek-v4-flash pricing is an ESTIMATE (the cheap/fast tier) pending
# confirmed official numbers — override with D2C_PRICING_* for accuracy.
MODEL_PRICING: dict[str, ModelPricing] = {
    "deepseek-v4-flash": ModelPricing(
        Decimal("0.56"), Decimal("1.68"), Decimal("0.07"), Decimal("0.56")
    ),
    "deepseek-v4-pro": ModelPricing(
        Decimal("1.20"), Decimal("3.60"), Decimal("0.12"), Decimal("1.20")
    ),
}


def _env_decimal(name: str) -> Decimal | None:
    import os

    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def cost_estimates_disabled() -> bool:
    import os

    return os.environ.get(ENV_DISABLE, "").strip().lower() in ("1", "true", "yes")


def pricing_for(model: str) -> ModelPricing | None:
    """Effective pricing: table defaults patched by env overrides.

    Env overrides alone are enough to price an unknown model (input+output
    required); otherwise unknown models return None (tokens still tracked).
    """
    base = MODEL_PRICING.get(model)
    env_in = _env_decimal(ENV_INPUT)
    env_out = _env_decimal(ENV_OUTPUT)
    env_cache = _env_decimal(ENV_CACHE_READ)

    if base is None:
        if env_in is None or env_out is None:
            return None
        return ModelPricing(env_in, env_out, env_cache or Decimal("0"), env_in)

    return ModelPricing(
        input_per_million=env_in if env_in is not None else base.input_per_million,
        output_per_million=env_out if env_out is not None else base.output_per_million,
        cache_read_per_million=(
            env_cache if env_cache is not None else base.cache_read_per_million
        ),
        cache_write_per_million=env_in if env_in is not None else base.cache_write_per_million,
    )


# ── Usage data model ──────────────────────────────────────────────────


@dataclass
class ModelUsage:
    """Token/cost accounting for one model call."""

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    estimated: bool = False  # True when tokens were estimated, not provider-reported
    estimated_cost_usd: Decimal = Decimal("0")
    cost_known: bool = True  # False: unknown model / estimates disabled


@dataclass
class SessionUsage:
    """Accumulated totals across a session's model calls."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    estimated_cost_usd: Decimal = Decimal("0")
    any_estimated: bool = False
    cost_known: bool = True

    def add(self, mu: ModelUsage) -> None:
        self.calls += 1
        self.input_tokens += mu.input_tokens
        self.output_tokens += mu.output_tokens
        self.cache_read_tokens += mu.cache_read_tokens
        self.cache_write_tokens += mu.cache_write_tokens
        self.estimated_cost_usd += mu.estimated_cost_usd
        self.any_estimated = self.any_estimated or mu.estimated
        self.cost_known = self.cost_known and mu.cost_known


def compute_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> tuple[Decimal, bool]:
    """Return (estimated_cost_usd, cost_known)."""
    if cost_estimates_disabled():
        return Decimal("0"), False
    pricing = pricing_for(model)
    if pricing is None:
        return Decimal("0"), False
    cost = (
        Decimal(input_tokens) * pricing.input_per_million
        + Decimal(output_tokens) * pricing.output_per_million
        + Decimal(cache_read_tokens) * pricing.cache_read_per_million
        + Decimal(cache_write_tokens) * pricing.cache_write_per_million
    ) / _MILLION
    return cost, True


# ── Extraction ────────────────────────────────────────────────────────


def extract_usage(
    response: Any,
    model: str,
    *,
    fallback_messages: list[dict] | None = None,
    fallback_text: str = "",
) -> ModelUsage:
    """Build a ModelUsage from a provider response.

    Reads Anthropic-style usage fields when present; otherwise estimates
    input from the sent messages and output from the response text, and
    marks the record as estimated.
    """
    usage = getattr(response, "usage", None)
    input_tokens = getattr(usage, "input_tokens", None)
    output_tokens = getattr(usage, "output_tokens", None)
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0

    estimated = input_tokens is None or output_tokens is None
    if estimated:
        from d2c.context import estimate_tokens

        if input_tokens is None:
            input_tokens = estimate_tokens(fallback_messages or [])
        if output_tokens is None:
            output_tokens = (
                estimate_tokens([{"role": "assistant", "content": fallback_text}])
                if fallback_text
                else 0
            )

    in_tok = int(input_tokens or 0)
    out_tok = int(output_tokens or 0)
    cost, cost_known = compute_cost(model, in_tok, out_tok, int(cache_read), int(cache_write))
    return ModelUsage(
        model=model,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cache_read_tokens=int(cache_read),
        cache_write_tokens=int(cache_write),
        estimated=estimated,
        estimated_cost_usd=cost,
        cost_known=cost_known,
    )


# ── Session tracker + global accessor ─────────────────────────────────


class UsageTracker:
    """Session-scoped accumulator. Reset on session switch."""

    def __init__(self) -> None:
        self.session = SessionUsage()

    def record(self, mu: ModelUsage) -> None:
        self.session.add(mu)

    def reset(self) -> None:
        self.session = SessionUsage()


_active_tracker: UsageTracker | None = None


def set_usage_tracker(tracker: UsageTracker | None) -> None:
    global _active_tracker
    _active_tracker = tracker


def get_usage_tracker() -> UsageTracker | None:
    return _active_tracker


def record_model_usage(
    model: str,
    response: Any,
    *,
    fallback_messages: list[dict] | None = None,
    fallback_text: str = "",
    turn_id: int | None = None,
) -> ModelUsage | None:
    """Extract usage, accumulate into the active tracker, emit the
    model_usage audit event. Never raises — usage accounting must not
    break the agent loop."""
    try:
        mu = extract_usage(
            response, model, fallback_messages=fallback_messages, fallback_text=fallback_text
        )
        tracker = get_usage_tracker()
        if tracker is not None:
            tracker.record(mu)

        from d2c.observability import audit

        audit(
            "model_usage",
            model=model,
            turn_id=turn_id,
            input_tokens=mu.input_tokens,
            output_tokens=mu.output_tokens,
            cache_read_tokens=mu.cache_read_tokens,
            cache_write_tokens=mu.cache_write_tokens,
            estimated=mu.estimated,
            estimated_cost_usd=str(mu.estimated_cost_usd) if mu.cost_known else None,
        )
        return mu
    except Exception:
        return None


def audit_session_usage(session: SessionUsage, session_id: str | None = None) -> None:
    """Emit the session_usage audit event (totals only, no prompt text)."""
    try:
        from d2c.observability import audit

        audit(
            "session_usage",
            session_id=session_id,
            calls=session.calls,
            input_tokens=session.input_tokens,
            output_tokens=session.output_tokens,
            cache_read_tokens=session.cache_read_tokens,
            cache_write_tokens=session.cache_write_tokens,
            estimated=session.any_estimated,
            estimated_cost_usd=str(session.estimated_cost_usd) if session.cost_known else None,
        )
    except Exception:
        pass


# ── Formatting ────────────────────────────────────────────────────────


def _fmt_cost(cost: Decimal) -> str:
    q = cost.quantize(Decimal("0.0001"))
    if q == q.quantize(Decimal("0.01")):
        q = q.quantize(Decimal("0.01"))
    return f"${q}"


def _fmt_compact(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def format_session_usage(session: SessionUsage, session_id: str | None = None) -> str:
    """Human-readable block for the /usage command."""
    lines = ["Session usage"]
    if session_id:
        lines[0] += f" ({session_id})"
    lines += [
        "",
        f"Model calls:   {session.calls:,}",
        f"Input tokens:  {session.input_tokens:,}",
        f"Output tokens: {session.output_tokens:,}",
        f"Cache read:    {session.cache_read_tokens:,}",
        f"Cache write:   {session.cache_write_tokens:,}",
    ]
    if cost_estimates_disabled():
        lines.append("Estimated cost: disabled (D2C_DISABLE_COST_ESTIMATES)")
    elif not session.cost_known:
        lines.append("Estimated cost: unknown (no pricing for model; set D2C_PRICING_*)")
    else:
        lines.append(f"Estimated cost: ~{_fmt_cost(session.estimated_cost_usd)} (estimate)")
    if session.any_estimated:
        lines.append("Note: some token counts were estimated (provider usage unavailable).")
    return "\n".join(lines)


def usage_status_fragment(session: SessionUsage) -> str:
    """Compact status-bar fragment, e.g. '133.4k in / 9.2k out | ~$0.42'."""
    frag = f"{_fmt_compact(session.input_tokens)} in / {_fmt_compact(session.output_tokens)} out"
    if session.cost_known and not cost_estimates_disabled():
        frag += f" | ~{_fmt_cost(session.estimated_cost_usd)}"
    return frag
