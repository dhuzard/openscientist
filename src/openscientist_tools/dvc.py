"""Typed MCP tools for DVC acquisition, assessment and governed analysis."""

from __future__ import annotations

from typing import Any

from openscientist.integrations.dvc import (
    DVCAcquisitionService,
    DVCAssessmentService,
    DVCAnalysisBlocked,
    DVCAnalysisError,
    DVCAnalysisRequest,
    DVCAnalysisService,
    DVCImportRequest,
)
from openscientist.integrations.dvc.approvals import DVCApprovalNotFound, FileDVCApprovalStore
from openscientist.integrations.dvc.credentials import DVCConnectionNotFound
from openscientist.integrations.dvc.service import DVCAcquisitionError
from openscientist.integrations.fair_prepare import FairPrepareError
from openscientist.preclinical_context.models import PreclinicalStudyContext
from openscientist_tools.server import mcp
from openscientist_tools.state import STATE


def _service() -> DVCAcquisitionService:
    return DVCAcquisitionService(STATE.job_dir)


def _analysis_service() -> DVCAnalysisService:
    return DVCAnalysisService(STATE.job_dir)


def _assessment_service() -> DVCAssessmentService:
    return DVCAssessmentService(STATE.job_dir)


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
    """Import one metric for a bounded cage/time window through UDWA."""
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
def dvc_assess_pre_analysis(dataset_id: str, context: dict[str, Any]) -> dict[str, Any]:
    """Assess study context with FAIR, PREPARE and ARRIVE before analysis."""
    try:
        study_context = PreclinicalStudyContext.model_validate(context)
        result = _assessment_service().pre_analysis(dataset_id, study_context)
        return {"ok": True, **result.model_dump(mode="json")}
    except (FairPrepareError, FileNotFoundError, ValueError) as exc:
        return _error(exc)


@mcp.tool()
def dvc_assess_post_analysis(dataset_id: str) -> dict[str, Any]:
    """Assess the generated DVC bundle after analysis."""
    try:
        result = _assessment_service().post_analysis(dataset_id)
        return {"ok": True, **result.model_dump(mode="json")}
    except (FairPrepareError, FileNotFoundError, ValueError) as exc:
        return _error(exc)


@mcp.tool()
def dvc_run_analysis(
    dataset_id: str,
    operation: str,
    context: dict[str, Any],
    parameters: dict[str, Any] | None = None,
    approval_id: str | None = None,
) -> dict[str, Any]:
    """Run one allowlisted UDWA operation after scientific and approval gates."""
    try:
        study_context = PreclinicalStudyContext.model_validate(context)
        approval = (
            FileDVCApprovalStore(STATE.job_dir).resolve(approval_id)
            if approval_id is not None
            else None
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
    except (DVCApprovalNotFound, DVCAnalysisBlocked, DVCAnalysisError, ValueError) as exc:
        return _error(exc)
