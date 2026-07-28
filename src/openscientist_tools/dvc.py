"""Typed MCP tools for DVC discovery and bounded dataset acquisition."""

from __future__ import annotations

from typing import Any

from openscientist.integrations.dvc import DVCImportRequest, DVCAcquisitionService
from openscientist.integrations.dvc.credentials import DVCConnectionNotFound
from openscientist.integrations.dvc.service import DVCAcquisitionError
from openscientist_tools.server import mcp
from openscientist_tools.state import STATE


def _service() -> DVCAcquisitionService:
    return DVCAcquisitionService(STATE.job_dir)


def _error(exc: Exception) -> dict[str, Any]:
    return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}


@mcp.tool()
def dvc_test_connection(connection_id: str = "default") -> dict[str, Any]:
    """Test a configured DVC connection without exposing its API key."""
    try:
        return {"ok": True, **_service().test_connection(connection_id)}
    except (DVCConnectionNotFound, DVCAcquisitionError, ValueError) as exc:
        return _error(exc)


@mcp.tool()
def dvc_list_metrics(connection_id: str = "default") -> dict[str, Any]:
    """List DVC metrics available to a configured connection."""
    try:
        metrics = _service().list_metrics(connection_id)
        return {"ok": True, "connection_id": connection_id, "metrics": metrics}
    except (DVCConnectionNotFound, DVCAcquisitionError, ValueError) as exc:
        return _error(exc)


@mcp.tool()
def dvc_search_cages(patterns: list[str], connection_id: str = "default") -> dict[str, Any]:
    """Search DVC cages by one or more identifier patterns."""
    try:
        cages = _service().search_cages(connection_id, patterns)
        return {"ok": True, "connection_id": connection_id, "cages": cages}
    except (DVCConnectionNotFound, DVCAcquisitionError, ValueError) as exc:
        return _error(exc)


@mcp.tool()
def dvc_import_dataset(
    cage_ids: list[str],
    metric_id: str,
    start: str,
    stop: str,
    aggregation: str = "MINUTE",
    connection_id: str = "default",
) -> dict[str, Any]:
    """Import one metric for a bounded cage/time window through UDWA.

    The raw archive and normalized tables remain in the current job directory.
    Only compact metadata, hashes, warnings and asset paths are returned.
    """
    try:
        request = DVCImportRequest(
            connection_id=connection_id,
            cage_ids=cage_ids,
            metric_id=metric_id,
            start=start,
            stop=stop,
            aggregation=aggregation,
        )
        result = _service().import_dataset(request)
        return {"ok": True, **result.model_dump()}
    except (DVCConnectionNotFound, DVCAcquisitionError, ValueError) as exc:
        return _error(exc)
