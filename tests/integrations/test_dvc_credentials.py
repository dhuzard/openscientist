from __future__ import annotations

import pytest

from openscientist.integrations.dvc.credentials import (
    DVCConnectionNotFound,
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
    with pytest.raises(DVCConnectionNotFound) as error:
        EnvironmentDVCConnectionProvider().resolve("missing")

    assert "API_KEY" not in str(error.value)
