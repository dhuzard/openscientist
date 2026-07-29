"""Credential redaction helpers for DVC responses and persisted artifacts."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_SENSITIVE_NAMES = {
    "apikey",
    "authorization",
    "credential",
    "credentials",
    "password",
    "refreshtoken",
    "secret",
    "token",
    "accesstoken",
}
_LABELLED_SECRET = re.compile(
    r"(?i)\b(api[_-]?key|apikey|access[_-]?token|refresh[_-]?token|token|"
    r"secret|password|credential|authorization)\b(\s*[:=]\s*)([^\s,;]+)"
)
_URL_USERINFO = re.compile(r"(?i)(https?://)([^/@\s]+)@")


def is_sensitive_key(key: object) -> bool:
    """Return whether a mapping key names credential-bearing data."""

    normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
    return normalized in _SENSITIVE_NAMES


def contains_sensitive_key(value: Any) -> bool:
    """Find credential-shaped keys recursively in agent-supplied structures."""

    if isinstance(value, Mapping):
        return any(
            is_sensitive_key(key) or contains_sensitive_key(item) for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(contains_sensitive_key(item) for item in value)
    return False


def redact_sensitive_text(value: str, *, secrets: tuple[str, ...] = ()) -> str:
    """Redact known secret values, labelled credentials, and URL userinfo."""

    redacted = value
    for secret in sorted((item for item in secrets if item), key=len, reverse=True):
        redacted = redacted.replace(secret, "[REDACTED]")
    redacted = _LABELLED_SECRET.sub(r"\1\2[REDACTED]", redacted)
    return _URL_USERINFO.sub(r"\1[REDACTED]@", redacted)


def redact_sensitive_data(value: Any, *, secrets: tuple[str, ...] = ()) -> Any:
    """Recursively redact credential-bearing keys and strings."""

    if isinstance(value, Mapping):
        return {
            key: (
                "[REDACTED]"
                if is_sensitive_key(key)
                else redact_sensitive_data(item, secrets=secrets)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive_data(item, secrets=secrets) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive_data(item, secrets=secrets) for item in value)
    if isinstance(value, str):
        return redact_sensitive_text(value, secrets=secrets)
    return value
