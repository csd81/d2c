"""Phase 83: DeepSeek official limits + pricing alignment."""

from __future__ import annotations

from decimal import Decimal

import pytest

from d2c.config import DEEPSEEK_MODEL_DEFAULTS, get_model_defaults
from d2c.loop import _model_output_cap
from d2c.usage import compute_cost

# ── limits ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("model", ["deepseek-v4-flash", "deepseek-v4-pro"])
def test_first_class_models_have_32k_output_and_128k_context(model):
    defaults = get_model_defaults(model)
    assert defaults["max_tokens"] == 32_000
    assert defaults["context_window"] == 128_000


def test_output_cap_uses_model_metadata():
    # First-class v4 models cap at their documented 32K max output.
    assert _model_output_cap("deepseek-v4-flash") == 32_000
    assert _model_output_cap("deepseek-v4-pro") == 32_000
    assert _model_output_cap("pro") == 32_000  # alias resolves


def test_output_cap_unknown_model_falls_back_safely():
    # Unknown/custom models fall back to the conservative default, never crash.
    assert _model_output_cap("some-random-model") == 8192


# ── pricing ─────────────────────────────────────────────────────────


def test_flash_is_free():
    cost, known = compute_cost(
        "deepseek-v4-flash",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_read_tokens=1_000_000,
        cache_write_tokens=1_000_000,
    )
    assert known is True
    assert cost == Decimal("0")


def test_pro_uses_official_paid_pricing():
    cost, known = compute_cost(
        "deepseek-v4-pro",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_read_tokens=1_000_000,
        cache_write_tokens=1_000_000,
    )
    assert known is True
    # 0.28 in + 0.42 out + 0.028 cache-read + 0.28 cache-write
    assert cost == Decimal("1.008")


def test_pricing_table_is_flash_and_pro_only():
    from d2c.usage import MODEL_PRICING

    assert set(MODEL_PRICING) == {"deepseek-v4-flash", "deepseek-v4-pro"}


def test_defaults_table_is_flash_and_pro_only():
    assert set(DEEPSEEK_MODEL_DEFAULTS) == {"deepseek-v4-flash", "deepseek-v4-pro"}
