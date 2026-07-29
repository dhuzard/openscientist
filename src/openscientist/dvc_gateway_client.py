"""Client transport for the trusted DVC acquisition gateway.

The untrusted tools process receives only this gateway URL and a short-lived,
job-scoped capability. DVC connection credentials remain in the web process.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from openscientist.integrations.dvc.security import redact_sensitive_text

DVC_GATEWAY_PORT = 8083
_WEB_HOST_ENV = "OPENSCIENTIST_WEB_HOST"
_DEFAULT_WEB_HOST = "openscientist"

DVC_GATEWAY_URL_ENV = "OPENSCIENTIST_DVC_GATEWAY_URL"
DVC_CAPABILITY_ENV = "OPENSCIENTIST_DVC_CAPABILITY"
DVC_CAPABILITY_HEADER = "x-openscientist-dvc-capability"

_DEFAULT_TIMEOUT = 900.0


class DVCGatewayError(RuntimeError):
    """A redacted, structured failure returned by the trusted gateway."""

    def __init__(self, message: str, *, code: str, retryable: bool = False) -> None:
        super().__init__(redact_sensitive_text(message))
        self.code = code
        self.retryable = retryable


def without_dvc_credentials(environment: dict[str, str]) -> dict[str, str]:
    """Remove direct DVC settings before crossing into an agent/MCP process."""

    return {
        key: value
        for key, value in environment.items()
        if not key.upper().startswith("DVC_")
    }


def container_dvc_gateway_base_url() -> str:
    """Gateway base URL reachable from a sibling agent container."""

    host = os.environ.get(_WEB_HOST_ENV, _DEFAULT_WEB_HOST)
    return f"http://{host}:{DVC_GATEWAY_PORT}"


def call_dvc_gateway(
    *,
    job_id: str,
    operation: str,
    arguments: dict[str, Any],
    timeout: float = _DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Execute one allowlisted acquisition operation for the authenticated job."""

    base_url = os.environ.get(DVC_GATEWAY_URL_ENV) or container_dvc_gateway_base_url()
    capability = os.environ.get(DVC_CAPABILITY_ENV, "")
    try:
        response = httpx.post(
            f"{base_url}/v1/acquire",
            json={
                "job_id": job_id,
                "operation": operation,
                "arguments": arguments,
            },
            headers={DVC_CAPABILITY_HEADER: capability},
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise DVCGatewayError(
            f"DVC gateway request failed: {exc}",
            code="gateway_unavailable",
            retryable=True,
        ) from exc

    try:
        payload: Any = response.json()
    except ValueError as exc:
        raise DVCGatewayError(
            "DVC gateway returned an invalid response.",
            code="invalid_gateway_response",
            retryable=response.status_code >= 500,
        ) from exc

    if response.status_code != 200:
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        message = error.get("message", "DVC gateway rejected the request.")
        code = error.get("code", "gateway_error")
        retryable = bool(error.get("retryable", response.status_code >= 500))
        raise DVCGatewayError(str(message), code=str(code), retryable=retryable)
    if not isinstance(payload, dict) or not isinstance(payload.get("result"), dict):
        raise DVCGatewayError(
            "DVC gateway returned an invalid result.",
            code="invalid_gateway_response",
        )
    return dict(payload["result"])
