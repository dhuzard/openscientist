"""Tests for `VllmProvider` (self-hosted, optionally keyed CodexCompatible provider)."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests

from openscientist import models
from openscientist.providers import vllm as vllm_mod
from openscientist.providers.base import (
    LLM_PROXY_URL_ENV,
    CodexCompatible,
    LlmUpstream,
    OpenAiWireCompatible,
)
from openscientist.providers.vllm import VllmProvider, _probe_vllm_context_tokens

_SETTINGS_PATH = "openscientist.providers.vllm.get_settings"
_PROBE_PATH = "openscientist.providers.vllm._probe_vllm_context_tokens"


def _settings(
    *,
    base_url: str = "http://localhost:8000/v1",
    model_default: str = "Qwen/Qwen3-32B",
    model: str | None = None,
    api_key: str | None = None,
    model_context_tokens: int | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        provider=SimpleNamespace(
            vllm_base_url=base_url,
            vllm_model=model_default,
            vllm_api_key=api_key,
            model=model,
            model_context_tokens=model_context_tokens,
        )
    )


@contextmanager
def _vllm(settings: SimpleNamespace | None = None) -> Iterator[VllmProvider]:
    """A provider reading ``settings`` (default config when omitted)."""
    with patch(_SETTINGS_PATH, return_value=settings or _settings()):
        yield VllmProvider()


def _resp(payload: object) -> MagicMock:
    """A fake requests.Response returning ``payload`` from .json()."""
    r = MagicMock()
    r.raise_for_status.return_value = None
    r.json.return_value = payload
    return r


def test_speaks_the_openai_wire_but_is_not_a_codex_backend() -> None:
    """Codex's MCP tool calls fail against non-gptoss models, so vLLM implements
    the OpenAI wire without claiming the Codex contract."""
    with _vllm() as p:
        assert isinstance(p, OpenAiWireCompatible)
        assert not isinstance(p, CodexCompatible)


def test_identity() -> None:
    with _vllm() as p:
        assert p.id == "vllm"
        assert p.display_name == "vLLM (self-hosted)"


# --- model name -----------------------------------------------------------------


def test_model_name_defaults_to_vllm_model() -> None:
    with _vllm() as p:
        assert p.effective_model_name() == "Qwen/Qwen3-32B"


def test_model_override_wins() -> None:
    with _vllm(_settings(model="meta-llama/Llama-3.3-70B-Instruct")) as p:
        assert p.effective_model_name() == "meta-llama/Llama-3.3-70B-Instruct"


# --- auth surface (keyless / keyed) ---------------------------------------------


def test_llm_upstream_is_keyless_without_api_key() -> None:
    with _vllm() as p:
        assert p.llm_upstream() == LlmUpstream("http://localhost:8000/v1", {})


def test_llm_upstream_injects_bearer_when_keyed() -> None:
    with _vllm(_settings(api_key="vk-secret")) as p:
        assert p.llm_upstream() == LlmUpstream(
            "http://localhost:8000/v1", {"authorization": "Bearer vk-secret"}
        )


def test_proxy_env_overrides_keyless_leaves_vllm_api_key_unset() -> None:
    with _vllm() as p:
        env = p.proxy_env_overrides(
            proxy_base_url="http://openscientist:8081", placeholder="job-1.tok"
        )
    assert env == {
        "OPENAI_API_KEY": "job-1.tok",
        LLM_PROXY_URL_ENV: "http://openscientist:8081",
    }


def test_proxy_env_overrides_replace_the_real_api_key() -> None:
    with _vllm(_settings(api_key="vk-secret")) as p:
        env = p.proxy_env_overrides(
            proxy_base_url="http://openscientist:8081", placeholder="job-1.tok"
        )
    # The real key must never reach the job container: the proxy holds it.
    assert env["VLLM_API_KEY"] == "job-1.tok"
    assert "vk-secret" not in env.values()


def test_container_env_omits_api_key_when_unset() -> None:
    env = VllmProvider.container_env(_settings().provider)
    assert env == {"VLLM_BASE_URL": "http://localhost:8000/v1", "VLLM_MODEL": "Qwen/Qwen3-32B"}


def test_container_env_carries_api_key_when_set() -> None:
    env = VllmProvider.container_env(_settings(api_key="vk-secret").provider)
    assert env["VLLM_API_KEY"] == "vk-secret"


# --- omp model catalog ------------------------------------------------------------


def test_omp_catalog_declares_the_served_model() -> None:
    """omp resolves --model against this, so id and contextWindow must be real."""
    with _vllm(_settings(model_context_tokens=262144)) as p:
        catalog = p.omp_model_catalog(context_window=262144)
    assert catalog is not None
    entry = catalog["providers"]["vllm"]
    assert entry["baseUrl"] == "http://localhost:8000/v1"
    assert entry["api"] == "openai-completions"
    # omp's schema accepts only apiKey, none or oauth.
    assert entry["auth"] == "none"
    assert "apiKey" not in entry
    model = entry["models"][0]
    assert model["id"] == "Qwen/Qwen3-32B"
    assert model["contextWindow"] == 262144


def test_omp_catalog_carries_the_api_key_when_keyed() -> None:
    with _vllm(_settings(api_key="vk-secret", model_context_tokens=4096)) as p:
        catalog = p.omp_model_catalog(context_window=4096)
    assert catalog is not None
    entry = catalog["providers"]["vllm"]
    assert entry["auth"] == "apiKey"
    assert entry["apiKey"] == "vk-secret"


def test_omp_catalog_points_at_the_proxy_with_the_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under the proxy omp must reach the proxy and authenticate as the job."""
    monkeypatch.setenv(LLM_PROXY_URL_ENV, "http://openscientist:8081")
    monkeypatch.setenv("OPENAI_API_KEY", "job-1.tok")
    with _vllm(_settings(api_key="vk-secret", model_context_tokens=4096)) as p:
        catalog = p.omp_model_catalog(context_window=4096)
    assert catalog is not None
    entry = catalog["providers"]["vllm"]
    assert entry["baseUrl"] == "http://openscientist:8081"
    assert entry["apiKey"] == "job-1.tok"
    # The real server key stays web-side with the proxy.
    assert "vk-secret" not in str(catalog)


def test_harness_env_points_at_vllm_without_proxy() -> None:
    with _vllm() as p:
        env = p.harness_env(proxy=None)
    # OpenAI clients reject an empty key, so a keyless server gets a dummy.
    assert env == {"OPENAI_BASE_URL": "http://localhost:8000/v1", "OPENAI_API_KEY": "vllm"}


def test_harness_env_uses_api_key_without_proxy() -> None:
    with _vllm(_settings(api_key="vk-secret")) as p:
        env = p.harness_env(proxy=None)
    assert env["OPENAI_API_KEY"] == "vk-secret"


def test_harness_env_points_at_proxy_when_active() -> None:
    with _vllm() as p:
        env = p.harness_env(proxy="http://openscientist:8081")
    assert env == {"OPENAI_BASE_URL": "http://openscientist:8081"}


# --- required config -------------------------------------------------------------


def test_required_config_errors_demand_a_model() -> None:
    errors = VllmProvider.required_config_errors(_settings(model_default="").provider)
    assert len(errors) == 1
    assert "VLLM_MODEL" in errors[0]


def test_missing_model_surfaces_as_a_config_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Requiring a served model must reach the operator, not crash the app."""
    from openscientist.providers import check_provider_config
    from openscientist.settings import clear_settings_cache

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENSCIENTIST_PROVIDER", "vllm")
    monkeypatch.delenv("VLLM_MODEL", raising=False)
    monkeypatch.delenv("OPENSCIENTIST_MODEL", raising=False)
    clear_settings_cache()
    try:
        configured, name, errors = check_provider_config()
    finally:
        clear_settings_cache()
    assert configured is False
    assert name == "vllm"
    assert any("VLLM_MODEL" in e for e in errors)


def test_validate_required_config_accepts_vllm_model() -> None:
    with _vllm() as p:
        assert p.validate_required_config() == []


def test_validate_required_config_accepts_openscientist_model() -> None:
    with _vllm(_settings(model_default="", model="Qwen/Qwen3-32B")) as p:
        assert p.validate_required_config() == []


def test_construction_fails_without_a_model() -> None:
    with (
        patch(_SETTINGS_PATH, return_value=_settings(model_default="")),
        pytest.raises(ValueError, match="VLLM_MODEL"),
    ):
        VllmProvider()


def test_get_cost_info_reports_zero_self_hosted_spend() -> None:
    with _vllm() as p:
        info = p.get_cost_info()
    assert info.total_spend_usd == 0.0
    assert info.recent_spend_usd == 0.0


def test_get_provider_selects_vllm(monkeypatch: pytest.MonkeyPatch) -> None:
    """`provider_id="vllm"` resolves to VllmProvider via the factory."""
    from openscientist.providers import get_provider
    from openscientist.settings import clear_settings_cache

    monkeypatch.setenv("OPENSCIENTIST_PROVIDER", "vllm")
    monkeypatch.setenv("VLLM_MODEL", "Qwen/Qwen3-32B")
    clear_settings_cache()
    try:
        assert isinstance(get_provider(), VllmProvider)
    finally:
        clear_settings_cache()


# --- context window probe (_probe_vllm_context_tokens) ---------------------------


def test_probe_reads_max_model_len_for_the_served_model() -> None:
    payload = {
        "data": [
            {"id": "other", "max_model_len": 4096},
            {"id": "Qwen/Qwen3-32B", "max_model_len": 40960},
        ]
    }
    with patch.object(vllm_mod.requests, "get", return_value=_resp(payload)) as get:
        assert _probe_vllm_context_tokens("http://h:8000/v1", "Qwen/Qwen3-32B", None) == 40960
    get.assert_called_once_with("http://h:8000/v1/models", headers={}, timeout=5)


def test_probe_strips_a_trailing_slash_from_the_base_url() -> None:
    payload = {"data": [{"id": "m", "max_model_len": 8192}]}
    with patch.object(vllm_mod.requests, "get", return_value=_resp(payload)) as get:
        assert _probe_vllm_context_tokens("http://h:8000/v1/", "m", None) == 8192
    get.assert_called_once_with("http://h:8000/v1/models", headers={}, timeout=5)


def test_probe_authenticates_against_a_keyed_server() -> None:
    """A server started with --api-key 401s an unauthenticated probe."""
    payload = {"data": [{"id": "m", "max_model_len": 8192}]}
    with patch.object(vllm_mod.requests, "get", return_value=_resp(payload)) as get:
        assert _probe_vllm_context_tokens("http://h:8000/v1", "m", "vk-secret") == 8192
    get.assert_called_once_with(
        "http://h:8000/v1/models",
        headers={"authorization": "Bearer vk-secret"},
        timeout=5,
    )


def test_probe_falls_back_to_the_only_served_model() -> None:
    """A one-model server answers even when the configured id spells it differently."""
    payload = {"data": [{"id": "served-alias", "max_model_len": 32768}]}
    with patch.object(vllm_mod.requests, "get", return_value=_resp(payload)):
        assert _probe_vllm_context_tokens("http://h:8000/v1", "Qwen/Qwen3-32B", None) == 32768


def test_probe_returns_none_when_several_models_and_no_match() -> None:
    payload = {"data": [{"id": "a", "max_model_len": 1}, {"id": "b", "max_model_len": 2}]}
    with patch.object(vllm_mod.requests, "get", return_value=_resp(payload)):
        assert _probe_vllm_context_tokens("http://h:8000/v1", "c", None) is None


def test_probe_returns_none_when_max_model_len_absent() -> None:
    """Older or non-vLLM OpenAI servers omit the field, which must not crash."""
    with patch.object(vllm_mod.requests, "get", return_value=_resp({"data": [{"id": "m"}]})):
        assert _probe_vllm_context_tokens("http://h:8000/v1", "m", None) is None


def test_probe_returns_none_on_unexpected_payload_shape() -> None:
    with patch.object(vllm_mod.requests, "get", return_value=_resp(["not", "a", "dict"])):
        assert _probe_vllm_context_tokens("http://h:8000/v1", "m", None) is None


def test_probe_returns_none_on_non_numeric_max_model_len() -> None:
    payload = {"data": [{"id": "m", "max_model_len": "lots"}]}
    with patch.object(vllm_mod.requests, "get", return_value=_resp(payload)):
        assert _probe_vllm_context_tokens("http://h:8000/v1", "m", None) is None


def test_probe_returns_none_on_connection_error() -> None:
    with patch.object(vllm_mod.requests, "get", side_effect=requests.ConnectionError("down")):
        assert _probe_vllm_context_tokens("http://h:8000/v1", "m", None) is None


# --- VllmProvider.model_profile (override / live probe / failure) ----------------


def test_model_profile_override_wins_without_probing() -> None:
    with (
        patch(_SETTINGS_PATH, return_value=_settings(model_context_tokens=65536)),
        patch(_PROBE_PATH) as probe,
    ):
        profile = VllmProvider().model_profile()
    assert profile.context_window_tokens == 65536
    probe.assert_not_called()


def test_model_profile_uses_live_probe() -> None:
    with (
        patch(_SETTINGS_PATH, return_value=_settings()),
        patch(_PROBE_PATH, return_value=40960) as probe,
    ):
        profile = VllmProvider().model_profile()
    assert profile.id == "Qwen/Qwen3-32B"
    assert profile.context_window_tokens == 40960
    probe.assert_called_once_with("http://localhost:8000/v1", "Qwen/Qwen3-32B", None)


def test_model_profile_probe_failure_logs_warning_and_defaults(caplog) -> None:
    with (
        patch(_SETTINGS_PATH, return_value=_settings()),
        patch(_PROBE_PATH, return_value=None),
        caplog.at_level(logging.WARNING, logger="openscientist.providers.vllm"),
    ):
        profile = VllmProvider().model_profile()
    assert profile.context_window_tokens == models._DEFAULT_CONTEXT_TOKENS
    # The warning names vLLM so an operator knows which server failed to answer.
    assert any("Could not probe the vLLM context window" in r.message for r in caplog.records)
