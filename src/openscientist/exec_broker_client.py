"""HTTP client for the web-side execution broker (httpx only, no docker).
The execute_code tool posts here instead of spawning containers over a socket.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

EXEC_BROKER_PORT = 8082
_WEB_HOST_ENV = "OPENSCIENTIST_WEB_HOST"
_DEFAULT_WEB_HOST = "openscientist"

EXEC_TOKEN_ENV = "OPENSCIENTIST_EXEC_TOKEN"
EXEC_BROKER_URL_ENV = "OPENSCIENTIST_EXEC_BROKER_URL"
EXEC_TOKEN_HEADER = "x-openscientist-exec-token"

# Wait past the executor's own timeout so a slow run still returns its result.
_HTTP_TIMEOUT_MARGIN = 60.0


class BrokerError(RuntimeError):
    """Raised when the broker call fails at the transport or protocol layer."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "broker_error",
        retryable: bool = False,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "retryable": self.retryable,
            "status_code": self.status_code,
            "details": self.details,
        }


def container_broker_base_url() -> str:
    """Broker base URL as reached from a sibling job container on the compose network."""
    host = os.environ.get(_WEB_HOST_ENV, _DEFAULT_WEB_HOST)
    return f"http://{host}:{EXEC_BROKER_PORT}"


def execute_code_via_broker(
    *,
    code: str,
    language: str,
    job_id: str,
    output_dir: str | dict[str, Any],
    timeout: int,
    data_path: str | dict[str, Any] | None = None,
    data_files: list[dict[str, Any]] | None = None,
    description: str = "",
    iteration: int = 0,
) -> dict[str, Any]:
    """POST an execute_code request using job-relative asset references."""
    base_url = os.environ.get(EXEC_BROKER_URL_ENV) or container_broker_base_url()
    token = os.environ.get(EXEC_TOKEN_ENV, "")
    payload: dict[str, Any] = {
        "code": code,
        "language": language,
        "job_id": job_id,
        "output_ref": output_dir,
        "data_ref": data_path,
        "data_files": data_files or [],
        "description": description,
        "iteration": iteration,
        "timeout": timeout,
    }
    try:
        response = httpx.post(
            f"{base_url}/execute",
            json=payload,
            headers={EXEC_TOKEN_HEADER: token},
            timeout=float(timeout) + _HTTP_TIMEOUT_MARGIN,
        )
    except httpx.HTTPError as exc:
        raise BrokerError(
            f"execution broker request failed: {exc}",
            code="broker_transport_error",
            retryable=True,
        ) from exc
    if response.status_code != 200:
        try:
            payload = response.json()
        except (AttributeError, ValueError):
            payload = {}
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            message = str(error.get("message") or "execution broker request failed")
            code = str(error.get("code") or "broker_http_error")
            retryable = bool(error.get("retryable"))
            details = error.get("details") if isinstance(error.get("details"), dict) else {}
        else:
            message = (
                f"execution broker returned HTTP {response.status_code}: {response.text[:500]}"
            )
            code = "broker_http_error"
            retryable = response.status_code >= 500
            details = {}
        raise BrokerError(
            message,
            code=code,
            retryable=retryable,
            status_code=response.status_code,
            details=details,
        )
    try:
        result: dict[str, Any] = response.json()
    except ValueError as exc:
        raise BrokerError(
            f"execution broker returned invalid JSON: {exc}",
            code="broker_protocol_error",
            retryable=True,
            status_code=response.status_code,
        ) from exc
    return result
