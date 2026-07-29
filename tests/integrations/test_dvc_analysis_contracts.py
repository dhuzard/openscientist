from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from openscientist.integrations.dvc.execution import (
    DVCAnalysisApproval,
    DVCAnalysisRequest,
    canonical_context_sha256,
    canonical_parameters_sha256,
    evaluate_prerequisites,
)
from openscientist.preclinical_context.models import (
    AcquisitionContext,
    EnvironmentContext,
    EvidenceStatus,
    EvidenceValue,
    PreclinicalStudyContext,
    StudyDesign,
)


def known(value: object) -> EvidenceValue:
    return EvidenceValue(value=value, status=EvidenceStatus.RECORDED, source="test")


def context(*, complete: bool = True) -> PreclinicalStudyContext:
    return PreclinicalStudyContext(
        study_id="study-1",
        design=StudyDesign(experimental_unit=known("cage") if complete else EvidenceValue()),
        environment=EnvironmentContext(
            timezone=known("Europe/Paris") if complete else EvidenceValue(),
            light_schedule=known({"lights_on": "07:00", "lights_off": "19:00"})
            if complete
            else EvidenceValue(),
        ),
        acquisition=AcquisitionContext(
            temporal_resolution=known("60 seconds") if complete else EvidenceValue()
        ),
    )


def request(operation: str, *, study_context: PreclinicalStudyContext, approved: bool = False):
    dataset_id = f"dvc-{uuid4()}"
    checkpoint_id = f"dvc-assess-{uuid4()}"
    approval = None
    if approved:
        approval = DVCAnalysisApproval(
            approval_id="approval-1",
            approved_by="scientist@example.org",
            approved_at=datetime.now(timezone.utc),
            dataset_id=dataset_id,
            pre_analysis_checkpoint_id=checkpoint_id,
            pre_analysis_checkpoint_sha256="a" * 64,
            operation=operation,
            context_sha256=canonical_context_sha256(study_context),
            parameters_sha256=canonical_parameters_sha256({}),
        )
    return DVCAnalysisRequest(
        dataset_id=dataset_id,
        pre_analysis_checkpoint_id=checkpoint_id,
        operation=operation,
        context=study_context,
        approval=approval,
    )


def test_sanity_check_does_not_require_approval():
    assert (
        evaluate_prerequisites(request("check_data_sanity", study_context=context(complete=False)))
        == []
    )


def test_light_dark_reports_all_missing_context_and_approval():
    blockers = evaluate_prerequisites(
        request("summarize_light_dark", study_context=context(complete=False))
    )
    assert "Missing required context: design.experimental_unit" in blockers
    assert "Missing required context: environment.timezone" in blockers
    assert "Missing required context: environment.light_schedule" in blockers
    assert "Human approval is required for summarize_light_dark." in blockers


def test_matching_approval_unblocks_operation():
    study_context = context()
    assert (
        evaluate_prerequisites(
            request("summarize_light_dark", study_context=study_context, approved=True)
        )
        == []
    )


def test_stale_approval_is_rejected():
    study_context = context()
    analysis_request = request("summarize_light_dark", study_context=study_context, approved=True)
    assert analysis_request.approval is not None
    analysis_request.approval.context_sha256 = "0" * 64
    assert any("Approval is stale" in item for item in evaluate_prerequisites(analysis_request))


def test_future_approval_is_rejected():
    study_context = context()
    analysis_request = request("summarize_light_dark", study_context=study_context, approved=True)
    assert analysis_request.approval is not None
    analysis_request.approval.approved_at = datetime.now(timezone.utc) + timedelta(days=1)
    assert any("future" in item for item in evaluate_prerequisites(analysis_request))


def test_unknown_operation_fails_closed():
    blockers = evaluate_prerequisites(request("arbitrary_python", study_context=context()))
    assert blockers == [
        "Operation 'arbitrary_python' is not approved for OpenScientist DVC execution."
    ]


def test_approval_timestamp_requires_timezone():
    with pytest.raises(ValueError, match="timezone"):
        DVCAnalysisApproval(
            approval_id="approval-1",
            approved_by="scientist",
            approved_at=datetime.now(),
            dataset_id=f"dvc-{uuid4()}",
            pre_analysis_checkpoint_id=f"dvc-assess-{uuid4()}",
            pre_analysis_checkpoint_sha256="a" * 64,
            operation="summarize_time_bins",
            context_sha256="0" * 64,
            parameters_sha256=canonical_parameters_sha256({}),
        )


def test_analysis_parameters_reject_credential_shaped_fields():
    with pytest.raises(ValueError, match="must not contain credentials"):
        DVCAnalysisRequest(
            dataset_id=f"dvc-{uuid4()}",
            pre_analysis_checkpoint_id=f"dvc-assess-{uuid4()}",
            operation="check_data_sanity",
            context=PreclinicalStudyContext(study_id="study-1"),
            parameters={"nested": {"api_key": "must-not-enter-provenance"}},
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("dataset_id", f"dvc-{uuid4()}", "Approval dataset"),
        (
            "pre_analysis_checkpoint_id",
            f"dvc-assess-{uuid4()}",
            "Approval checkpoint",
        ),
        ("parameters_sha256", "0" * 64, "Approval parameters"),
    ],
)
def test_mismatched_approval_scope_is_rejected(field, value, message):
    study_context = context()
    analysis_request = request("summarize_time_bins", study_context=study_context, approved=True)
    assert analysis_request.approval is not None
    setattr(analysis_request.approval, field, value)

    assert any(message in item for item in evaluate_prerequisites(analysis_request))
