"""Security and contract tests for the trusted DVC acquisition gateway."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from starlette.testclient import TestClient

from openscientist import dvc_gateway
from openscientist.dvc_gateway import create_dvc_gateway_app
from openscientist.dvc_gateway_client import (
    DVC_CAPABILITY_HEADER,
    DVCGatewayError,
    call_dvc_gateway,
)
from openscientist.integrations.dvc.service import DVCAcquisitionError
from openscientist.job_container.secrets import (
    make_dvc_capability,
    verify_dvc_capability,
)
from openscientist_tools import dvc as dvc_tools

_MASTER = "gateway-master-key"
_NOW = 2_000_000_000


@pytest.fixture
def jobs_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "jobs"
    root.mkdir()
    monkeypatch.setattr(
        dvc_gateway,
        "get_settings",
        lambda: SimpleNamespace(
            container=SimpleNamespace(container_app_dir=str(tmp_path)),
        ),
    )
    return root


def _client(service: MagicMock) -> TestClient:
    return TestClient(
        create_dvc_gateway_app(
            master_key=lambda: _MASTER,
            service_factory=lambda _job_dir: service,
        )
    )


def _token(job_id: str, *, now: int | None = None, ttl: int = 3600) -> str:
    return make_dvc_capability(
        _MASTER,
        job_id,
        now=_NOW if now is None else now,
        ttl_seconds=ttl,
    )


def _post(
    client: TestClient,
    *,
    job_id: str = "job-1",
    token: str | None = None,
    operation: str = "test_connection",
    arguments: dict[str, Any] | None = None,
) -> Any:
    return client.post(
        "/v1/acquire",
        json={
            "job_id": job_id,
            "operation": operation,
            "arguments": arguments or {"connection_id": "default"},
        },
        headers={DVC_CAPABILITY_HEADER: token or _token(job_id)},
    )


def test_capability_is_job_scoped_time_bounded_and_tamper_evident() -> None:
    token = _token("job-1")
    assert verify_dvc_capability(_MASTER, token, expected_job_id="job-1", now=_NOW)
    assert not verify_dvc_capability(_MASTER, token, expected_job_id="job-2", now=_NOW)
    assert not verify_dvc_capability(_MASTER, token, expected_job_id="job-1", now=_NOW + 3601)
    assert not verify_dvc_capability(
        _MASTER, token[:-1] + ("0" if token[-1] != "0" else "1"), expected_job_id="job-1", now=_NOW
    )


def test_gateway_resolves_job_path_server_side_and_dispatches(
    jobs_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (jobs_root / "job-1").mkdir()
    service = MagicMock()
    service.test_connection.return_value = {
        "connection_id": "default",
        "accepted": True,
    }
    monkeypatch.setattr("openscientist.job_container.secrets.time.time", lambda: _NOW)

    response = _post(_client(service))

    assert response.status_code == 200
    assert response.json()["result"]["accepted"] is True
    service.test_connection.assert_called_once_with("default")


def test_gateway_persists_scientific_state_after_successful_dispatch(
    jobs_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_dir = jobs_root / "job-1"
    job_dir.mkdir()
    service = MagicMock()
    service.test_connection.return_value = {"accepted": True}
    persisted: list[tuple[str, Path]] = []

    async def persist(job_id: str, resolved_job_dir: Path) -> None:
        persisted.append((job_id, resolved_job_dir))

    monkeypatch.setattr("openscientist.job_container.secrets.time.time", lambda: _NOW)
    client = TestClient(
        create_dvc_gateway_app(
            master_key=lambda: _MASTER,
            service_factory=lambda _job_dir: service,
            state_persister=persist,
        )
    )

    response = _post(client)

    assert response.status_code == 200
    assert persisted == [("job-1", job_dir)]


def test_gateway_rejects_capability_owned_by_another_job(
    jobs_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (jobs_root / "job-1").mkdir()
    service = MagicMock()
    monkeypatch.setattr("openscientist.job_container.secrets.time.time", lambda: _NOW)

    response = _post(_client(service), token=_token("job-2"))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_capability"
    service.test_connection.assert_not_called()


def test_gateway_rejects_non_allowlisted_operation(
    jobs_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (jobs_root / "job-1").mkdir()
    service = MagicMock()
    monkeypatch.setattr("openscientist.job_container.secrets.time.time", lambda: _NOW)

    response = _post(_client(service), operation="read_environment")

    assert response.status_code == 403
    service.test_connection.assert_not_called()


def test_gateway_rejects_missing_job_without_creating_it(
    jobs_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = MagicMock()
    monkeypatch.setattr("openscientist.job_container.secrets.time.time", lambda: _NOW)

    response = _post(_client(service), job_id="missing-job")

    assert response.status_code == 404
    assert not (jobs_root / "missing-job").exists()


def test_upstream_failure_is_structured_and_redacted(
    jobs_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (jobs_root / "job-1").mkdir()
    service = MagicMock()
    service.test_connection.side_effect = DVCAcquisitionError(
        "upstream rejected api_key=super-secret"
    )
    monkeypatch.setattr("openscientist.job_container.secrets.time.time", lambda: _NOW)

    response = _post(_client(service))

    assert response.status_code == 502
    payload = response.json()
    assert payload["error"]["code"] == "upstream_dvc_error"
    assert payload["error"]["retryable"] is True
    assert "super-secret" not in response.text
    assert "[REDACTED]" in response.text


def test_client_raises_actionable_structured_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Response:
        status_code = 401

        @staticmethod
        def json() -> dict[str, Any]:
            return {
                "error": {
                    "code": "invalid_capability",
                    "message": "DVC capability is missing or expired.",
                    "retryable": False,
                }
            }

    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: _Response())
    monkeypatch.setenv("OPENSCIENTIST_DVC_CAPABILITY", "expired")

    with pytest.raises(DVCGatewayError) as caught:
        call_dvc_gateway(job_id="job-1", operation="test_connection", arguments={})

    assert caught.value.code == "invalid_capability"
    assert caught.value.retryable is False
    assert "expired" in str(caught.value)


def test_agent_tool_routes_acquisition_through_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = MagicMock(
        return_value={
            "connection_id": "lab",
            "metrics": [{"id": "ACTIVITY"}],
        }
    )
    monkeypatch.setattr(dvc_tools, "call_dvc_gateway", gateway)

    result = dvc_tools.dvc_list_metrics("lab")

    assert result["ok"] is True
    assert result["metrics"] == [{"id": "ACTIVITY"}]
    gateway.assert_called_once_with(
        job_id=dvc_tools.STATE.job_id,
        operation="list_metrics",
        arguments={"connection_id": "lab"},
    )
