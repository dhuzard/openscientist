"""Tests for model-price estimation fallbacks."""

from unittest.mock import patch

import pytest

from openscientist.providers.pricing import estimate_cost_usd


def test_gpt_5_5_fallback_price_when_remote_catalog_lags() -> None:
    with patch("openscientist.providers.pricing._get_litellm_pricing", return_value={}):
        cost = estimate_cost_usd("gpt-5.5", input_tokens=13_547, output_tokens=2_495)

    assert cost == pytest.approx(0.142585)


def test_estimate_cost_uses_catalog_cache_rates_and_output_rate_for_reasoning() -> None:
    catalog = {
        "priced-model": {
            "input_cost_per_token": 2e-6,
            "output_cost_per_token": 8e-6,
            "cache_read_input_token_cost": 0.2e-6,
            "cache_creation_input_token_cost": 2.5e-6,
        }
    }
    with patch("openscientist.providers.pricing._get_litellm_pricing", return_value=catalog):
        cost = estimate_cost_usd(
            "priced-model",
            input_tokens=100,
            output_tokens=20,
            cache_read_tokens=50,
            cache_write_tokens=10,
            reasoning_tokens=5,
        )

    assert cost == pytest.approx(100 * 2e-6 + 20 * 8e-6 + 50 * 0.2e-6 + 10 * 2.5e-6 + 5 * 8e-6)


def test_estimate_cost_falls_back_to_base_rates_for_extra_categories() -> None:
    catalog = {
        "base-rates-only": {
            "input_cost_per_token": 2e-6,
            "output_cost_per_token": 8e-6,
        }
    }
    with patch("openscientist.providers.pricing._get_litellm_pricing", return_value=catalog):
        cost = estimate_cost_usd(
            "base-rates-only",
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=50,
            cache_write_tokens=10,
            reasoning_tokens=5,
        )

    assert cost == pytest.approx(60 * 2e-6 + 5 * 8e-6)
