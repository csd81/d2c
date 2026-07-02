"""Phase 81: deepseek-v4-flash default + narrowed model surface."""

from __future__ import annotations

import pytest

from d2c.config import (
    DEEPSEEK_MODEL_ALIASES,
    DEEPSEEK_MODEL_DEFAULTS,
    Config,
    resolve_model,
)


def test_default_model_is_flash(monkeypatch):
    monkeypatch.delenv("D2C_MODEL", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert Config().model == "deepseek-v4-flash"
    assert Config.load().model == "deepseek-v4-flash"


@pytest.mark.parametrize(
    ("alias", "canonical"),
    [
        ("flash", "deepseek-v4-flash"),
        ("v4-flash", "deepseek-v4-flash"),
        ("deepseek-v4-flash", "deepseek-v4-flash"),
        ("pro", "deepseek-v4-pro"),
        ("v4", "deepseek-v4-pro"),
        ("v4-pro", "deepseek-v4-pro"),
        ("deepseek-v4-pro", "deepseek-v4-pro"),
        ("FLASH", "deepseek-v4-flash"),  # case-insensitive
    ],
)
def test_alias_resolution(alias, canonical):
    assert resolve_model(alias) == canonical


@pytest.mark.parametrize("removed", ["chat", "v3", "reasoner", "r1"])
def test_removed_aliases_are_gone(removed):
    # No longer advertised; they pass through as raw custom strings.
    assert removed not in DEEPSEEK_MODEL_ALIASES
    assert resolve_model(removed) == removed


def test_first_class_models_are_flash_and_pro_only():
    assert set(DEEPSEEK_MODEL_DEFAULTS) == {"deepseek-v4-flash", "deepseek-v4-pro"}
    # flash listed first (the default)
    assert next(iter(DEEPSEEK_MODEL_DEFAULTS)) == "deepseek-v4-flash"


def test_custom_model_still_passes_through():
    assert resolve_model("some-custom-model") == "some-custom-model"


def test_pricing_covers_flash_and_pro():
    from d2c.usage import pricing_for

    assert pricing_for("deepseek-v4-flash") is not None
    assert pricing_for("deepseek-v4-pro") is not None
    # flash is the cheaper tier
    assert (
        pricing_for("deepseek-v4-flash").input_per_million
        < pricing_for("deepseek-v4-pro").input_per_million
    )
