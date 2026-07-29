"""vLLM provider (self-hosted open-weight models on an OpenAI-compatible wire).

Routes an agent at a self-hosted vLLM server (default
``http://localhost:8000/v1``). A vLLM server serves exactly the model it was
launched with, so ``VLLM_MODEL`` (or ``OPENSCIENTIST_MODEL``) must name it.
Auth is optional: a server started with ``--api-key`` needs ``VLLM_API_KEY``,
otherwise the provider is keyless like Ollama.

Driven by omp, not by Codex. It speaks the OpenAI wire, but Codex's MCP tool
calls come back "unsupported call" against non-gptoss models, which degrades a
run to writing files instead of recording artifacts, so no Codex contract is
implemented here.

Because the server runs outside the app, the base URL must be reachable
from wherever the agent runs. In-process on the dev box ``localhost`` works
directly. From inside the agent container, point ``VLLM_BASE_URL`` at the
host (for example ``http://host.docker.internal:8000/v1``).
"""

from __future__ import annotations

import logging
import os

import requests

from openscientist.models import ModelProfile, probed_model_profile
from openscientist.providers.base import (
    LLM_PROXY_URL_ENV,
    CostInfo,
    LlmUpstream,
    OmpModelCatalog,
    OpenAiWireCompatible,
    env_from_pairs,
    self_hosted_omp_model_catalog,
)
from openscientist.settings import ProviderSettings, get_settings

logger = logging.getLogger(__name__)


def _probe_vllm_context_tokens(base_url: str, model_id: str, api_key: str | None) -> int | None:
    """Read the served context window from vLLM's ``GET /v1/models``.

    vLLM fixes the window at launch (``--max-model-len``) and reports it as
    ``max_model_len`` on every model card. A server started with ``--api-key``
    rejects an unauthenticated probe, so the credential is required whenever one
    is in play. Returns None on any failure so the caller can fall back.
    """
    headers = {"authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        resp = requests.get(f"{base_url.rstrip('/')}/models", headers=headers, timeout=5)
        resp.raise_for_status()
        payload = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.debug("vLLM /v1/models probe failed: %s", exc)
        return None

    data = payload.get("data") if isinstance(payload, dict) else None
    cards = [c for c in data if isinstance(c, dict)] if isinstance(data, list) else []
    card = next((c for c in cards if c.get("id") == model_id), None)
    # A one-model server is the common deployment, so its single card answers
    # the question even when the configured id spells the model differently.
    if card is None and len(cards) == 1:
        card = cards[0]
    if card is None:
        return None
    try:
        window = int(card.get("max_model_len") or 0)
    except (TypeError, ValueError):
        logger.debug("vLLM reported a non-numeric max_model_len for %s", model_id)
        return None
    return window or None


class VllmProvider(OpenAiWireCompatible):
    """Self-hosted vLLM server driven by omp (open-weight models)."""

    @property
    def id(self) -> str:
        return "vllm"

    display_name = "vLLM (self-hosted)"

    @classmethod
    def container_env(
        cls, provider: ProviderSettings, *, gcp_credentials_container_path: str | None = None
    ) -> dict[str, str]:
        return env_from_pairs(
            [
                ("VLLM_BASE_URL", provider.vllm_base_url),
                ("VLLM_MODEL", provider.vllm_model),
                ("VLLM_API_KEY", provider.vllm_api_key),
            ]
        )

    def harness_env(self, *, proxy: str | None) -> dict[str, str]:
        if proxy:
            return super().harness_env(proxy=proxy)
        # Point an OpenAI-family harness straight at vLLM. A keyless server
        # still needs a non-empty key because OpenAI clients require one.
        s = get_settings().provider
        return {"OPENAI_BASE_URL": s.vllm_base_url, "OPENAI_API_KEY": s.vllm_api_key or "vllm"}

    def validate_required_config(self) -> list[str]:
        return self.required_config_errors(get_settings().provider)

    @classmethod
    def required_config_errors(cls, provider: ProviderSettings) -> list[str]:
        # The base URL has a usable default and the key is optional, but a vLLM
        # server has no default served model, so the operator must name it.
        if provider.model or provider.vllm_model:
            return []
        return ["VLLM_MODEL (or OPENSCIENTIST_MODEL) must name the model the vLLM server serves."]

    def get_cost_info(self, lookback_hours: int = 24) -> CostInfo:
        # Self-hosted inference has no per-call API cost. Report zero spend so
        # the budget checks pass cleanly rather than warning on missing data.
        return CostInfo(
            provider_name=self.display_name,
            total_spend_usd=0.0,
            recent_spend_usd=0.0,
            recent_period_hours=lookback_hours,
            data_lag_note="Self-hosted vLLM inference incurs no API cost.",
        )

    def llm_upstream(self) -> LlmUpstream | None:
        s = get_settings().provider
        headers = {"authorization": f"Bearer {s.vllm_api_key}"} if s.vllm_api_key else {}
        return LlmUpstream(s.vllm_base_url, headers)

    def proxy_env_overrides(self, *, proxy_base_url: str, placeholder: str) -> dict[str, str]:
        # omp authenticates with OPENAI_API_KEY, so the container
        # sends the placeholder and the proxy swaps in the real credential.
        env = {"OPENAI_API_KEY": placeholder, LLM_PROXY_URL_ENV: proxy_base_url}
        if get_settings().provider.vllm_api_key:
            # Overwrite rather than ship the real key into the job container.
            env["VLLM_API_KEY"] = placeholder
        return env

    def _endpoint(self) -> tuple[str, str | None]:
        """Base URL and credential to reach the server.

        Inside a proxied container the endpoint is the proxy and the credential
        is the job placeholder, because the real key stays web-side.
        """
        proxy = os.environ.get(LLM_PROXY_URL_ENV)
        s = get_settings().provider
        if proxy:
            return proxy, os.environ.get("OPENAI_API_KEY")
        return s.vllm_base_url, s.vllm_api_key

    def effective_model_name(self) -> str | None:
        s = get_settings().provider
        return s.model or s.vllm_model or None

    def model_profile(self) -> ModelProfile:
        # A self-hosted window is whatever --max-model-len the server was
        # launched with, so probe it rather than trusting a trained maximum.
        # Probing the same endpoint the agent uses keeps it working when the
        # container may only reach the proxy.
        base_url, key = self._endpoint()
        return probed_model_profile(
            model_id=self.effective_model_name(),
            override=get_settings().provider.model_context_tokens,
            probe=lambda mid: _probe_vllm_context_tokens(base_url, mid, key),
            server_name="vLLM",
            provider_logger=logger,
        )

    def omp_model_catalog(self, *, context_window: int) -> OmpModelCatalog | None:
        model_id = self.effective_model_name()
        if not model_id:
            return None
        base_url, key = self._endpoint()
        return self_hosted_omp_model_catalog(
            provider_id=self.id,
            name=self.display_name,
            base_url=base_url,
            model_id=model_id,
            context_window=context_window,
            api_key=key,
        )
