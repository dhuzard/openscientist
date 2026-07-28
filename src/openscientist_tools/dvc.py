"""Typed MCP tools for DVC acquisition and governed deterministic analysis."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from openscientist.integrations.dvc import (
    DVCAcquisitionService,
    DVCAnalysisApproval,
    DVCAnalysisBlocked,
    DVCAnalysisError,
    DVCAnalysisRequest,
    DVCAnalysisService,
    DVCImportRequest,
)
from openscientist.integrations.dvc.credentials import DVCConnectionNotFound
from openscientist.integrations.dvc.service import DVCAcquisitionError
from openscientist.preclinical_context.models import PreclinicalStudyContext
from openscientist_tools.server import mcp
from openscientist_tools.state import STATE


def _service() -> DVCAcquisitionService:
    return DVCAcquisitionService(STATE.job_dir)


def _analysis_service() -> DVCAnalysisService:
    return DVCAnalysisService(STATE.job_dir)


def _error(exc: Exception) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "error": str(exc),
        "error_type": type(exc).__name__,
    }
    if isinstance(exc, DVCAnalysisBlocked):
        payload["blockers"] = exc.blockers
    return payload


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

    Full data remain in the current job directory. Only compact metadata, hashes,
    warnings and asset paths are returned.
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


@mcp.tool()
def dvc_run_analysis(
    dataset_id: str,
    operation: str,
    context: dict[str, Any],
    parameters: dict[str, Any] | None = None,
    approval_id: str | None = None,
    approved_by: str | None = None,
    approved_at: str | None = None,
    approval_context_sha256: str | None = None,
) -> dict[str, Any]:
    """Run one allowlisted UDWA operation after scientific and approval gates.

    Context must use the versioned OpenScientist preclinical-context schema.
    For operations requiring approval, all four approval fields are mandatory and
    the context hash must match the exact submitted context.
    """
    try:
        study_context = PreclinicalStudyContext.model_validate(context)
        approval = None
        approval_values = (
            approval_id,
            approved_by,
            approved_at,
            approval_context_sha256,
        )
        if any(value is not None for value in approval_values):
            if not all(value is not None for value in approval_values):
                raise ValueError("All approval fields must be supplied together.")
            approval = DVCAnalysisApproval(
                approval_id=approval_id,
                approved_by=approved_by,
                approved_at=datetime.fromisoformat(approved_at.replace("Z", "+00:00")),
                operation=operation,
                context_sha256=approval_context_sha256,
            )
        request = DVCAnalysisRequest(
            dataset_id=dataset_id,
            operation=operation,
            context=study_context,
            parameters=parameters or {},
            approval=approval,
        )
        result = _analysis_service().execute(request)
        return {"ok": True, **result.model_dump(mode="json")}
    except (DVCAnalysisBlocked, DVCAnalysisError, ValueError) as exc:
        return _error(exc)
