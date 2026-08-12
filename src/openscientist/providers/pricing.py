"""LLM model pricing lookup via the litellm pricing database."""

import logging
import re
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

_LITELLM_PRICING_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
)
_CACHE_TTL_SECONDS = 86_400  # 24 hours

_cache: dict[str, Any] = {}
_cache_fetched_at: float = 0.0


def _get_litellm_pricing() -> dict[str, Any]:
    """Fetch (and cache for 24h) the litellm model pricing database."""
    global _cache, _cache_fetched_at
    if _cache and (time.monotonic() - _cache_fetched_at) < _CACHE_TTL_SECONDS:
        return _cache
    try:
        resp = requests.get(_LITELLM_PRICING_URL, timeout=10)
        resp.raise_for_status()
        _cache = resp.json()
        _cache_fetched_at = time.monotonic()
        logger.debug("Fetched litellm pricing database (%d entries)", len(_cache))
    except Exception as e:
        logger.warning("Failed to fetch litellm pricing database: %s", e)
        if not _cache:
            _cache = _FALLBACK_PRICING
    return _cache


def normalize_model_name(model: str) -> str:
    """
    Strip provider-specific prefixes/suffixes to get a litellm-compatible key.

    Examples:
      us.anthropic.claude-sonnet-4-5-20250929-v1:0  ->  claude-sonnet-4-5
      claude-sonnet-4-5@20250929                    ->  claude-sonnet-4-5
      claude-sonnet-4-6                             ->  claude-sonnet-4-6 (unchanged)
    """
    # Bedrock: remove leading region prefix like "us.anthropic." or "eu.anthropic."
    model = re.sub(r"^(?:us|eu|ap)\.anthropic\.", "", model)
    # Bedrock: remove trailing version suffix like "-20250929-v1:0"
    model = re.sub(r"-\d{8}.*$", "", model)
    # Vertex AI: remove revision suffix like "@20250929"
    model = re.sub(r"@\d{8}$", "", model)
    return model


def estimate_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    reasoning_tokens: int = 0,
) -> float:
    """
    Estimate cost in USD using the litellm pricing database.

    Prices every billed bucket, not just input and output. Cached reads dominate
    agentic runs -- each turn resends the conversation -- so omitting them
    understates cost badly, and the caller cannot tell because the total is still
    a plausible number. Reasoning tokens are billed as output where a provider
    reports them separately.

    Falls back to the input rate for cache reads/writes when the entry carries no
    cache-specific rate: wrong, but closer than charging zero.

    Tries exact key first, then the normalised model name. Returns 0.0 and warns
    if no pricing entry is found, so a silent zero is at least visible in logs.
    """
    pricing = _get_litellm_pricing()
    entry = pricing.get(model) or pricing.get(normalize_model_name(model))
    if not entry:
        logger.warning(
            "No pricing entry for model %r (normalised %r); reporting $0.00. "
            "Cost tracking for this model is not meaningful until a rate is added.",
            model,
            normalize_model_name(model),
        )
        return 0.0
    in_rate = float(entry.get("input_cost_per_token", 0.0))
    out_rate = float(entry.get("output_cost_per_token", 0.0))
    read_rate = float(entry.get("cache_read_input_token_cost", in_rate))
    write_rate = float(entry.get("cache_creation_input_token_cost", in_rate))
    return (
        in_rate * input_tokens
        + out_rate * output_tokens
        + read_rate * cache_read_tokens
        + write_rate * cache_write_tokens
        + out_rate * reasoning_tokens
    )


# Fallback used only when the remote fetch fails and the cache is empty.
_FALLBACK_PRICING: dict[str, Any] = {
    "claude-opus-4-6": {"input_cost_per_token": 15e-6, "output_cost_per_token": 75e-6},
    "claude-sonnet-4-6": {"input_cost_per_token": 3e-6, "output_cost_per_token": 15e-6},
    "claude-sonnet-4-5": {"input_cost_per_token": 3e-6, "output_cost_per_token": 15e-6},
    "claude-sonnet-4-20250514": {"input_cost_per_token": 3e-6, "output_cost_per_token": 15e-6},
    "claude-haiku-4-5": {"input_cost_per_token": 0.8e-6, "output_cost_per_token": 4e-6},
}
