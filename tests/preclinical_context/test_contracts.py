from __future__ import annotations

import pytest
from pydantic import ValidationError

from openscientist.preclinical_context import (
    AssessmentStatus,
    EvidenceValue,
    PreclinicalStudyContext,
    StubPreclinicalAssessmentProvider,
)
from openscientist.preclinical_context.models import EvidenceStatus


def test_evidence_value_enforces_epistemic_state() -> None:
    with pytest.raises(ValidationError):
        EvidenceValue(value="cage", status=EvidenceStatus.UNKNOWN)
    with pytest.raises(ValidationError):
        EvidenceValue(value="cage", status=EvidenceStatus.INFERRED)

    inferred = EvidenceValue(
        value="cage",
        status=EvidenceStatus.INFERRED,
        confidence=0.8,
        source="review",
    )
    assert inferred.confidence == 0.8


def test_context_schema_is_versioned_and_strict() -> None:
    context = PreclinicalStudyContext(study_id="study-1")
    assert context.schema_version == "openscientist-preclinical-context/0.1"

    with pytest.raises(ValidationError):
        PreclinicalStudyContext(study_id="study-1", unexpected=True)


def test_stub_provider_never_claims_framework_satisfaction() -> None:
    provider = StubPreclinicalAssessmentProvider()
    context = PreclinicalStudyContext(study_id="study-1")

    results = provider.assess_context(context, frameworks=("FAIR", "PREPARE"))

    assert [result.framework for result in results] == ["FAIR", "PREPARE"]
    assert all(
        finding.status in {AssessmentStatus.MISSING, AssessmentStatus.PARTIAL}
        for result in results
        for finding in result.findings
    )
    assert all(result.context_hash == results[0].context_hash for result in results)


def test_stub_provider_blocks_inference_without_experimental_unit() -> None:
    result = StubPreclinicalAssessmentProvider().assess_context(
        PreclinicalStudyContext(study_id="study-1"),
        frameworks=("PREPARE",),
    )[0]

    assert "inferential_analysis" in result.findings[0].blocks
    assert "design.experimental_unit" in result.findings[0].missing_fields
