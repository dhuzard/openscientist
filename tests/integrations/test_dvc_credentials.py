from __future__ import annotations

from pathlib import Path

import pytest

from openscientist.integrations.dvc.credentials import (
    DVCConnectionNotFoundError,
    EnvironmentDVCConnectionProvider,
)


def test_default_connection_uses_existing_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DVC_API_KEY", "top-secret")
    monkeypatch.setenv("DVC_BASE_URL", "https://example.invalid")

    connection = EnvironmentDVCConnectionProvider().resolve("default")

    assert connection.connection_id == "default"
    assert connection.api_key == "top-secret"
    assert connection.base_url == "https://example.invalid"
    assert "top-secret" not in repr({"connection_id": connection.connection_id})


def test_named_connection_is_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DVC_CONNECTION_TECNIPLAST_LAB_API_KEY", "secret")

    connection = EnvironmentDVCConnectionProvider().resolve("tecniplast-lab")

    assert connection.api_key == "secret"


def test_unknown_connection_fails_without_listing_environment() -> None:
    with pytest.raises(DVCConnectionNotFoundError) as error:
        EnvironmentDVCConnectionProvider().resolve("missing")

    assert "API_KEY" not in str(error.value)


def test_agent_image_uses_buildkit_secret_for_private_udwa_install() -> None:
    dockerfile = (Path(__file__).parents[2] / "Dockerfile.agent").read_text(encoding="utf-8")

    assert "--mount=type=secret,id=github_token,required=true" in dockerfile
    assert "GIT_ASKPASS" in dockerfile
    assert "ARG GITHUB_TOKEN" not in dockerfile
    assert "ENV GITHUB_TOKEN" not in dockerfile
    assert "https://x-access-token:" not in dockerfile
