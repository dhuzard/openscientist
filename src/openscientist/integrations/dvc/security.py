"""Compatibility exports for domain-neutral assay redaction helpers."""

from __future__ import annotations

from openscientist.assays.security import (
    contains_sensitive_key,
    is_sensitive_key,
    redact_sensitive_data,
    redact_sensitive_text,
)

__all__ = [
    "contains_sensitive_key",
    "is_sensitive_key",
    "redact_sensitive_data",
    "redact_sensitive_text",
]
