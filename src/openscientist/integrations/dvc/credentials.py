"""Server-side DVC connection resolution.

Agents and MCP clients pass only a logical ``connection_id``. API keys are
resolved inside the tools process and are never included in returned models.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Protocol


class DVCConnectionNotFound(LookupError):
    """Raised when a logical connection cannot be resolved."""


@dataclass(frozen=True)
class DVCConnection:
    connection_id: str
    api_key: str
    base_url: str | None = None


class DVCConnectionProvider(Protocol):
    def resolve(self, connection_id: str) -> DVCConnection: ...


def _environment_prefix(connection_id: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", connection_id).strip("_").upper()
    if not normalized:
        raise DVCConnectionNotFound("DVC connection id must contain letters or numbers.")
    return f"DVC_CONNECTION_{normalized}"


class EnvironmentDVCConnectionProvider:
    """Resolve logical connections from process environment variables.

    ``connection_id='default'`` supports the existing ``DVC_API_KEY`` and
    ``DVC_BASE_URL`` variables. Named connections use
    ``DVC_CONNECTION_<ID>_API_KEY`` and optional ``..._BASE_URL``.
    """

    def resolve(self, connection_id: str) -> DVCConnection:
        connection_id = connection_id.strip()
        if not connection_id:
            raise DVCConnectionNotFound("DVC connection id is required.")

        if connection_id.casefold() == "default":
            api_key = os.getenv("DVC_API_KEY")
            base_url = os.getenv("DVC_BASE_URL")
        else:
            prefix = _environment_prefix(connection_id)
            api_key = os.getenv(f"{prefix}_API_KEY")
            base_url = os.getenv(f"{prefix}_BASE_URL")

        if not api_key:
            raise DVCConnectionNotFound(
                f"DVC connection {connection_id!r} is not configured for this tool server."
            )
        return DVCConnection(connection_id=connection_id, api_key=api_key, base_url=base_url)
