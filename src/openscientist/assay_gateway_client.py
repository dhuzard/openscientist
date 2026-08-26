"""Client transport for the contract-driven trusted assay gateway."""

from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

from openscientist.assays.security import redact_sensitive_text

ASSAY_GATEWAY_PORT = 8083
ASSAY_CAPABILITY_HEADER = "x-openscientist-assay-capability"
ASSAY_GATEWAY_URL_ENV = "OPENSCIENTIST_ASSAY_GATEWAY_URL"
ASSAY_CAPABILITY_ENV = "OPENSCIENTIST_ASSAY_CAPABILITY"
ASSAY_CAPABILITIES_ENV = "OPENSCIENTIST_ASSAY_CAPABILITIES"
_DEFAULT_TIMEOUT = 900.0
_ASSAY_ID = re.compile(r"^[a-z][a-z0-9_.-]{1,99}$")


class AssayGatewayError(RuntimeError):
    def __init__(self, message: str, *, code: str, retryable: bool = False) -> None:
        super().__init__(redact_sensitive_text(message))
        self.code = code
        self.retryable = retryable


def container_assay_gateway_base_url() -> str:
    host = os.environ.get("OPENSCIENTIST_WEB_HOST", "openscientist")
    return f"http://{host}:{ASSAY_GATEWAY_PORT}"


def call_assay_gateway(
    *,
    job_id: str,
    assay_id: str,
    action: str,
    arguments: dict[str, Any],
    timeout: float = _DEFAULT_TIMEOUT,
) -> Any:
    if not _ASSAY_ID.fullmatch(assay_id):
        raise ValueError("Invalid assay id.")
    base_url = os.environ.get(ASSAY_GATEWAY_URL_ENV) or container_assay_gateway_base_url()
    capability = os.environ.get(ASSAY_CAPABILITY_ENV, "")
    encoded_capabilities = os.environ.get(ASSAY_CAPABILITIES_ENV, "")
    if encoded_capabilities:
        try:
            capability_map = json.loads(encoded_capabilities)
        except json.JSONDecodeError as exc:
            raise AssayGatewayError(
                "Assay capability map is invalid.", code="invalid_capability_environment"
            ) from exc
        if not isinstance(capability_map, dict):
            raise AssayGatewayError(
                "Assay capability map is invalid.", code="invalid_capability_environment"
            )
        selected = capability_map.get(assay_id)
        capability = selected if isinstance(selected, str) else ""
    try:
        response = httpx.post(
            f"{base_url}/v1/assays/{assay_id}/acquire",
            json={"job_id": job_id, "action": action, "arguments": arguments},
            headers={ASSAY_CAPABILITY_HEADER: capability},
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise AssayGatewayError(
            f"Assay gateway request failed: {exc}",
            code="gateway_unavailable",
            retryable=True,
        ) from exc
    try:
        payload: Any = response.json()
    except ValueError as exc:
        raise AssayGatewayError(
            "Assay gateway returned an invalid response.",
            code="invalid_gateway_response",
            retryable=response.status_code >= 500,
        ) from exc
    if response.status_code != 200:
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        raise AssayGatewayError(
            str(error.get("message", "Assay gateway rejected the request.")),
            code=str(error.get("code", "gateway_error")),
            retryable=bool(error.get("retryable", response.status_code >= 500)),
        )
    if not isinstance(payload, dict) or "result" not in payload:
        raise AssayGatewayError(
            "Assay gateway returned an invalid result.",
            code="invalid_gateway_response",
        )
    return payload["result"]
