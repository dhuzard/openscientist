"""Tests for `openscientist.providers.pricing`.

The pricing table is fetched from litellm at runtime, so these patch it rather
than asserting real rates, which would break whenever litellm updates.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

from openscientist.providers.pricing import estimate_cost_usd, normalize_model_name

_ENTRY = {
    "input_cost_per_token": 5e-6,
    "output_cost_per_token": 25e-6,
    "cache_read_input_token_cost": 0.5e-6,
    "cache_creation_input_token_cost": 6.25e-6,
}


def _patched(entry: dict[str, float] | None = None) -> object:
    return patch(
        "openscientist.providers.pricing._get_litellm_pricing",
        return_value={"m": entry if entry is not None else _ENTRY},
    )


def test_every_billed_bucket_is_priced() -> None:
    """Cached reads dominate agentic runs; omitting them understates badly."""
    with _patched():
        cost = estimate_cost_usd("m", 1_000_000, 1_000_000, 1_000_000, 1_000_000, 1_000_000)
    # input 5 + output 25 + cache read 0.5 + cache write 6.25 + reasoning at output 25
    assert cost == 61.75


def test_reasoning_tokens_bill_as_output() -> None:
    with _patched():
        assert estimate_cost_usd("m", 0, 0, 0, 0, 1_000_000) == 25.0


def test_cache_buckets_default_to_zero_so_old_callers_are_unchanged() -> None:
    with _patched():
        assert estimate_cost_usd("m", 1_000_000, 1_000_000) == 30.0


def test_cache_rates_fall_back_to_the_input_rate_when_absent() -> None:
    """Wrong, but closer than charging nothing for a real billed bucket."""
    with _patched({"input_cost_per_token": 2e-6, "output_cost_per_token": 8e-6}):
        assert estimate_cost_usd("m", 0, 0, 1_000_000, 1_000_000) == 4.0


def test_unknown_model_warns_rather_than_silently_returning_zero(
    caplog: object,
) -> None:
    """A silent 0.0 is indistinguishable from a genuinely free run, which is how
    prod recorded $0 for every Opus job on an unrecognised deployment name."""
    with _patched(), caplog.at_level(logging.WARNING):  # type: ignore[attr-defined]
        assert estimate_cost_usd("not-a-model", 1_000_000, 1_000_000) == 0.0
    assert "No pricing entry" in caplog.text  # type: ignore[attr-defined]


def test_azure_deployment_names_do_not_resolve() -> None:
    """Documents a real gap: `claude-opus-4-8-2` is prod's deployment name, not a
    model id, so it finds no entry. Bedrock/Vertex suffixes are handled; Azure
    Foundry aliases are not."""
    assert normalize_model_name("claude-opus-4-8-2") == "claude-opus-4-8-2"
    assert normalize_model_name("us.anthropic.claude-sonnet-4-5-20250929-v1:0") == (
        "claude-sonnet-4-5"
    )
