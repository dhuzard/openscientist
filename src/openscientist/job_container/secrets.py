"""Per-job secret derivation for the untrusted agent container."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import time
from collections.abc import Iterable

_JOB_SECRET_LABEL = "job_secret:"
_LLM_PROXY_LABEL = "llm_proxy:"
_EXEC_TOKEN_LABEL = "exec_token:"
_DVC_CAPABILITY_LABEL = "dvc_capability:"
_ASSAY_CAPABILITY_LABEL = "assay_capability:"
_PLACEHOLDER_SEP = "."
_DVC_CAPABILITY_TTL_SECONDS = 4 * 60 * 60
_DVC_CAPABILITY_MAX_TTL_SECONDS = 24 * 60 * 60
_ASSAY_CAPABILITY_TTL_SECONDS = 4 * 60 * 60
_ASSAY_CAPABILITY_MAX_TTL_SECONDS = 24 * 60 * 60
_SCOPE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def derive_job_secret(master_key: str, job_id: str) -> str:
    """Return HMAC-SHA256(master_key, "job_secret:" + job_id) as a 64-char hex digest.

    Keeps the master key out of the job container while satisfying the required
    Settings.secret_key field inside it.
    """
    message = f"{_JOB_SECRET_LABEL}{job_id}".encode()
    return hmac.new(master_key.encode(), message, hashlib.sha256).hexdigest()


def derive_llm_proxy_token(master_key: str, job_id: str) -> str:
    """Per-job token presented to the LLM proxy, keyed by a distinct label."""
    message = f"{_LLM_PROXY_LABEL}{job_id}".encode()
    return hmac.new(master_key.encode(), message, hashlib.sha256).hexdigest()


def make_job_placeholder(master_key: str, job_id: str) -> str:
    """Placeholder LLM credential: "<job_id>.<token>", verifiable by the proxy."""
    return f"{job_id}{_PLACEHOLDER_SEP}{derive_llm_proxy_token(master_key, job_id)}"


def verify_job_placeholder(master_key: str, placeholder: str) -> bool:
    """True when placeholder is a valid "<job_id>.<token>" for the master key."""
    job_id, sep, token = placeholder.rpartition(_PLACEHOLDER_SEP)
    if sep != _PLACEHOLDER_SEP or not job_id or not token:
        return False
    return hmac.compare_digest(derive_llm_proxy_token(master_key, job_id), token)


def derive_exec_token(master_key: str, job_id: str) -> str:
    """Per-job token the execution broker verifies, keyed by a distinct label."""
    message = f"{_EXEC_TOKEN_LABEL}{job_id}".encode()
    return hmac.new(master_key.encode(), message, hashlib.sha256).hexdigest()


def make_exec_placeholder(master_key: str, job_id: str) -> str:
    """Execution credential "<job_id>.<token>" the broker recomputes and verifies."""
    return f"{job_id}{_PLACEHOLDER_SEP}{derive_exec_token(master_key, job_id)}"


def make_dvc_capability(
    master_key: str,
    job_id: str,
    *,
    now: int | None = None,
    ttl_seconds: int = _DVC_CAPABILITY_TTL_SECONDS,
) -> str:
    """Create a time-bounded capability tied to exactly one job id."""

    if not 60 <= ttl_seconds <= _DVC_CAPABILITY_MAX_TTL_SECONDS:
        raise ValueError("DVC capability TTL must be between 60 seconds and 24 hours.")
    issued_at = int(time.time()) if now is None else now
    expires_at = issued_at + ttl_seconds
    payload = f"{job_id}.{issued_at}.{expires_at}"
    signature = hmac.new(
        master_key.encode(),
        f"{_DVC_CAPABILITY_LABEL}{payload}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload}.{signature}"


def verify_dvc_capability(
    master_key: str,
    capability: str,
    *,
    expected_job_id: str,
    now: int | None = None,
) -> bool:
    """Validate signature, ownership, issue time, expiry and maximum lifetime."""

    try:
        job_id, issued_raw, expires_raw, signature = capability.rsplit(_PLACEHOLDER_SEP, 3)
        issued_at = int(issued_raw)
        expires_at = int(expires_raw)
    except (TypeError, ValueError):
        return False
    current = int(time.time()) if now is None else now
    if job_id != expected_job_id:
        return False
    if issued_at > current + 30 or expires_at <= current:
        return False
    if expires_at <= issued_at or expires_at - issued_at > _DVC_CAPABILITY_MAX_TTL_SECONDS:
        return False
    payload = f"{job_id}.{issued_at}.{expires_at}"
    expected = hmac.new(
        master_key.encode(),
        f"{_DVC_CAPABILITY_LABEL}{payload}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def _encode_capability_payload(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(encoded).decode().rstrip("=")


def _decode_capability_payload(encoded: str) -> dict[str, object] | None:
    try:
        padding = "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded + padding))
    except (ValueError, TypeError, UnicodeError, binascii.Error):
        return None
    return payload if isinstance(payload, dict) else None


def make_assay_capability(
    master_key: str,
    job_id: str,
    assay_id: str,
    permissions: Iterable[str],
    *,
    now: int | None = None,
    ttl_seconds: int = _ASSAY_CAPABILITY_TTL_SECONDS,
) -> str:
    """Create a signed capability scoped to one job, assay, and permission set."""

    if not _SCOPE_NAME.fullmatch(job_id) or not _SCOPE_NAME.fullmatch(assay_id):
        raise ValueError("Job and assay identifiers must be valid capability scope names.")
    normalized_permissions = tuple(sorted(set(permissions)))
    if not normalized_permissions or any(
        not _SCOPE_NAME.fullmatch(permission) for permission in normalized_permissions
    ):
        raise ValueError("At least one valid assay permission is required.")
    if not 60 <= ttl_seconds <= _ASSAY_CAPABILITY_MAX_TTL_SECONDS:
        raise ValueError("Assay capability TTL must be between 60 seconds and 24 hours.")

    issued_at = int(time.time()) if now is None else now
    encoded = _encode_capability_payload(
        {
            "assay_id": assay_id,
            "expires_at": issued_at + ttl_seconds,
            "issued_at": issued_at,
            "job_id": job_id,
            "permissions": normalized_permissions,
            "version": 1,
        }
    )
    signature = hmac.new(
        master_key.encode(),
        f"{_ASSAY_CAPABILITY_LABEL}{encoded}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{encoded}.{signature}"


def verify_assay_capability(
    master_key: str,
    capability: str,
    *,
    expected_job_id: str,
    expected_assay_id: str,
    required_permission: str,
    now: int | None = None,
) -> bool:
    """Validate an assay capability without trusting caller-supplied scope."""

    encoded, separator, signature = capability.rpartition(_PLACEHOLDER_SEP)
    if separator != _PLACEHOLDER_SEP or not encoded or not signature:
        return False
    expected_signature = hmac.new(
        master_key.encode(),
        f"{_ASSAY_CAPABILITY_LABEL}{encoded}".encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected_signature, signature):
        return False
    payload = _decode_capability_payload(encoded)
    if payload is None or payload.get("version") != 1:
        return False
    if payload.get("job_id") != expected_job_id or payload.get("assay_id") != expected_assay_id:
        return False
    permissions = payload.get("permissions")
    if not isinstance(permissions, list) or required_permission not in permissions:
        return False
    issued_at = payload.get("issued_at")
    expires_at = payload.get("expires_at")
    if not isinstance(issued_at, int) or not isinstance(expires_at, int):
        return False
    current = int(time.time()) if now is None else now
    if issued_at > current + 30 or expires_at <= current:
        return False
    if expires_at <= issued_at or expires_at - issued_at > _ASSAY_CAPABILITY_MAX_TTL_SECONDS:
        return False
    return True
