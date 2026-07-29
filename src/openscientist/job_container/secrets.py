"""Per-job secret derivation for the untrusted agent container."""

from __future__ import annotations

import hashlib
import hmac
import time

_JOB_SECRET_LABEL = "job_secret:"
_LLM_PROXY_LABEL = "llm_proxy:"
_EXEC_TOKEN_LABEL = "exec_token:"
_DVC_CAPABILITY_LABEL = "dvc_capability:"
_PLACEHOLDER_SEP = "."
_DVC_CAPABILITY_TTL_SECONDS = 4 * 60 * 60
_DVC_CAPABILITY_MAX_TTL_SECONDS = 24 * 60 * 60


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
