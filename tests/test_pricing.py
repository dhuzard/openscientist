"""Tests for model-price estimation fallbacks."""

from unittest.mock import patch

import pytest

from openscientist.providers.pricing import estimate_cost_usd


def test_gpt_5_5_fallback_price_when_remote_catalog_lags() -> None:
    with patch("openscientist.providers.pricing._get_litellm_pricing", return_value={}):
        cost = estimate_cost_usd("gpt-5.5", input_tokens=13_547, output_tokens=2_495)

    assert cost == pytest.approx(0.142585)
