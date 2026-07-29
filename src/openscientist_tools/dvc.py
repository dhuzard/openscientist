"""Typed MCP tools for DVC acquisition, assessment and governed analysis."""

from __future__ import annotations

from typing import Any, Literal

from openscientist.dvc_gateway_client import DVCGatewayError, call_dvc_gateway
from openscientist.integrations.dvc import (
    DVCAnalysisBlockedError,
    DVCAnalysisError,
    DVCAnalysisRequest,
    DVCAnalysisService,
    DVCAssessmentService,
    DVCImportRequest,
)
from openscientist.integrations.dvc.approvals import (
    DVCApprovalNotFoundError,
    FileDVCApprovalStore,
)
from openscientist.integrations.dvc.security import redact_sensitive_text
from openscientist.integrations.fair_prepare import FairPrepareError
from openscientist.preclinical_context.models import PreclinicalStudyContext
from openscientist_tools.server import mcp
from openscientist_tools.state import STATE


def _acquire(operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return call_dvc_gateway(
        job_id=STATE.job_id,
        operation=operation,
        arguments=arguments,
    )


def _analysis_service() -> DVCAnalysisService:
    return DVCAnalysisService(STATE.job_dir)


def _assessment_service() -> DVCAssessmentService:
    return DVCAssessmentService(STATE.job_dir)


def _error(exc: Exception) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "error": redact_sensitive_text(str(exc)),
        "error_type": type(exc).__name__,
    }
    if isinstance(exc, DVCAnalysisBlockedError):
        payload["blockers"] = exc.blockers
    if isinstance(exc, DVCGatewayError):
        payload["error_code"] = exc.code
        payload["retryable"] = exc.retryable
    return payload


@mcp.tool()
def dvc_test_connection(connection_id: str = "default") -> dict[str, Any]:
    """Test a configured DVC connection without exposing its API key."""
    try:
        return {"ok": True, **_acquire("test_connection", {"connection_id": connection_id})}
    except (DVCGatewayError, ValueError) as exc:
        return _error(exc)


@mcp.tool()
def dvc_list_metrics(connection_id: str = "default") -> dict[str, Any]:
    """List DVC metrics available to a configured connection."""
    try:
        return {"ok": True, **_acquire("list_metrics", {"connection_id": connection_id})}
    except (DVCGatewayError, ValueError) as exc:
        return _error(exc)


@mcp.tool()
def dvc_search_cages(patterns: list[str], connection_id: str = "default") -> dict[str, Any]:
    """Search DVC cages by one or more identifier patterns."""
    try:
        return {
            "ok": True,
            **_acquire(
                "search_cages",
                {"connection_id": connection_id, "patterns": patterns},
            ),
        }
    except (DVCGatewayError, ValueError) as exc:
        return _error(exc)


@mcp.tool()
def dvc_import_dataset(
    cage_ids: list[str],
    metric_id: str,
    start: str,
    stop: str,
    aggregation: Literal["MINUTE", "HOUR"] = "MINUTE",
    connection_id: str = "default",
) -> dict[str, Any]:
    """Import a bounded metric through UDWA using cage humanReadableId values, not UUIDs."""
    try:
        request = DVCImportRequest(
            connection_id=connection_id,
            cage_ids=cage_ids,
            metric_id=metric_id,
            start=start,
            stop=stop,
            aggregation=aggregation,
        )
        return {"ok": True, **_acquire("import_dataset", request.model_dump(mode="json"))}
    except (DVCGatewayError, ValueError) as exc:
        return _error(exc)


@mcp.tool()
def dvc_assess_pre_analysis(dataset_id: str, context: dict[str, Any]) -> dict[str, Any]:
    """Assess a strict nested study context with FAIR, PREPARE and ARRIVE.

    Context uses EvidenceValue ``status`` (not ``state``) and nested ``design``,
    ``animals``, ``environment``, and ``acquisition`` sections. Cage IDs and
    import time bounds belong to the immutable dataset manifest, not context.
    """
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
    except (DVCAnalysisBlockedError, FairPrepareError, FileNotFoundError, ValueError) as exc:
        return _error(exc)


@mcp.tool()
def dvc_run_analysis(
    dataset_id: str,
    pre_analysis_checkpoint_id: str,
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
            pre_analysis_checkpoint_id=pre_analysis_checkpoint_id,
            operation=operation,
            context=study_context,
            parameters=parameters or {},
            approval=approval,
        )
        result = _analysis_service().execute(request)
        return {"ok": True, **result.model_dump(mode="json")}
    except (
        DVCApprovalNotFoundError,
        DVCAnalysisBlockedError,
        DVCAnalysisError,
        ValueError,
    ) as exc:
        return _error(exc)
