from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from openscientist.preclinical_context import (
    ClockSynchronization,
    CohortNode,
    DeviceNode,
    EntityReference,
    EntityType,
    EvidenceValue,
    HousingAssignment,
    HousingUnitNode,
    InterventionNode,
    ObservationNode,
    OutcomeNode,
    PreclinicalStudyContext,
    PreclinicalStudyContextV2,
    RepeatedMeasureDefinition,
    SessionNode,
    StubPreclinicalAssessmentProvider,
    StudyDesignV2,
    StudyNode,
    StudyUnitDefinitions,
    SubjectNode,
    UnitDefinition,
)
from openscientist.preclinical_context.models import (
    AcquisitionContext,
    AnimalContext,
    EnvironmentContext,
    EvidenceStatus,
    StudyDesign,
)


def recorded[T](value: T) -> EvidenceValue[T]:
    return EvidenceValue[T](value=value, status=EvidenceStatus.RECORDED, source="test")


def complete_graph() -> PreclinicalStudyContextV2:
    started = datetime(2026, 1, 2, 9, tzinfo=timezone.utc)
    ended = datetime(2026, 1, 2, 9, 30, tzinfo=timezone.utc)
    subject_unit = UnitDefinition(
        entity_type=EntityType.SUBJECT,
        label="animal",
        entity_ids=["subject-1"],
    )
    return PreclinicalStudyContextV2(
        study=StudyNode(study_id="study-1", objective=recorded("locomotor activity")),
        design=StudyDesignV2(
            units=StudyUnitDefinitions(
                assignment=recorded(subject_unit),
                experimental=recorded(subject_unit),
                observational=recorded(subject_unit),
                analysis=recorded(subject_unit),
            ),
            repeated_measures=[
                recorded(
                    RepeatedMeasureDefinition(
                        repeated_unit=subject_unit,
                        within_unit_factors=["session"],
                        expected_count=2,
                        interval_seconds=86_400,
                    )
                )
            ],
        ),
        cohorts=[CohortNode(cohort_id="cohort-1", study_id="study-1")],
        subjects=[SubjectNode(subject_id="subject-1", cohort_id="cohort-1")],
        housing_units=[HousingUnitNode(housing_unit_id="cage-1", study_id="study-1")],
        housing_assignments=[
            HousingAssignment(
                assignment_id="housing-1",
                subject_id="subject-1",
                housing_unit_id="cage-1",
                starts_at=recorded(started),
            )
        ],
        devices=[
            DeviceNode(
                device_id="camera-1",
                study_id="study-1",
                housing_unit_id="cage-1",
                system=recorded("open-field tracker"),
            )
        ],
        sessions=[
            SessionNode(
                session_id="session-1",
                study_id="study-1",
                subject_ids=["subject-1"],
                housing_unit_ids=["cage-1"],
                device_ids=["camera-1"],
                starts_at=recorded(started),
                ends_at=recorded(ended),
                timezone=recorded("Europe/Paris"),
                clock_synchronization=ClockSynchronization(
                    reference_device_id="camera-1",
                    method=recorded("NTP"),
                    uncertainty_seconds=recorded(0.02),
                    synchronized_at=recorded(started),
                ),
            )
        ],
        interventions=[
            InterventionNode(
                intervention_id="intervention-1",
                study_id="study-1",
                targets=[EntityReference(entity_type=EntityType.SUBJECT, entity_id="subject-1")],
                intervention_type=recorded("compound administration"),
                starts_at=recorded(started),
            )
        ],
        outcomes=[
            OutcomeNode(
                outcome_id="distance",
                study_id="study-1",
                name=recorded("distance travelled"),
                unit=recorded("cm"),
            )
        ],
        observations=[
            ObservationNode(
                observation_id="observation-1",
                session_id="session-1",
                outcome_id="distance",
                observed_units=[
                    EntityReference(entity_type=EntityType.SUBJECT, entity_id="subject-1")
                ],
                observed_at=recorded(ended),
                value=recorded(123.4),
            )
        ],
    )


def test_v2_models_complete_typed_experimental_graph() -> None:
    graph = complete_graph()

    assert graph.schema_version == "openscientist-preclinical-context/0.2"
    assert graph.study_id == "study-1"
    assert graph.observations[0].value.value == 123.4
    assert graph.design.units.analysis.value is not None
    assert graph.design.units.analysis.value.entity_type == EntityType.SUBJECT
    assert PreclinicalStudyContextV2.model_validate(graph.model_dump(mode="json")) == graph


def test_v2_rejects_dangling_relationships_and_duplicate_ids() -> None:
    payload = complete_graph().model_dump(mode="json")
    payload["sessions"][0]["subject_ids"] = ["missing-subject"]
    with pytest.raises(ValidationError, match="references missing subject"):
        PreclinicalStudyContextV2.model_validate(payload)

    payload = complete_graph().model_dump(mode="json")
    payload["devices"][0]["device_id"] = "subject-1"
    with pytest.raises(ValidationError, match="globally unique"):
        PreclinicalStudyContextV2.model_validate(payload)

    payload = complete_graph().model_dump(mode="json")
    payload["sessions"][0]["subject_ids"] = ["subject-1", "subject-1"]
    with pytest.raises(ValidationError, match="duplicate subject_ids"):
        PreclinicalStudyContextV2.model_validate(payload)


def test_v2_requires_iana_timezone_and_aware_timestamps() -> None:
    with pytest.raises(ValidationError, match="unknown IANA timezone"):
        SessionNode(session_id="session", study_id="study", timezone=recorded("Mars/Base"))

    with pytest.raises(ValidationError, match="UTC offset"):
        SessionNode(
            session_id="session",
            study_id="study",
            starts_at=recorded(datetime(2026, 1, 2, 9)),
        )


def test_typed_evidence_rejects_wrong_value_type_and_preserves_epistemic_rules() -> None:
    with pytest.raises(ValidationError):
        EvidenceValue[int](
            value="not-an-integer",  # type: ignore[arg-type]
            status=EvidenceStatus.RECORDED,
        )
    with pytest.raises(ValidationError, match="inferred values require confidence"):
        EvidenceValue[str](value="animal", status=EvidenceStatus.INFERRED)


def test_v0_1_round_trip_preserves_values_status_and_provenance() -> None:
    legacy = PreclinicalStudyContext(
        study_id="study-legacy",
        objective=recorded({"question": "activity"}),
        design=StudyDesign(
            experimental_unit=EvidenceValue(
                value="cage",
                status=EvidenceStatus.INFERRED,
                confidence=0.8,
                source="protocol review",
            )
        ),
        animals=AnimalContext(species=recorded("Mus musculus")),
        environment=EnvironmentContext(timezone=recorded("Europe/Paris")),
        acquisition=AcquisitionContext(system=recorded("DVC")),
        asset_ids=["asset-1"],
        evidence_ids=["evidence-1"],
    )

    graph = PreclinicalStudyContextV2.from_v0_1(legacy)

    assert graph.design.units.experimental.value is not None
    assert graph.design.units.experimental.value.entity_type == EntityType.HOUSING_UNIT
    assert graph.design.units.experimental.status == EvidenceStatus.INFERRED
    assert graph.design.units.experimental.confidence == 0.8
    assert graph.to_v0_1().model_dump(mode="json") == legacy.model_dump(mode="json")


def test_stub_assessment_provider_accepts_v2_and_uses_v2_paths() -> None:
    context = PreclinicalStudyContextV2(study=StudyNode(study_id="study-1"))

    result = StubPreclinicalAssessmentProvider().assess_context(
        context,
        frameworks=("PREPARE",),
    )[0]

    assert result.findings[0].missing_fields == [
        "design.units.experimental",
        "study.objective",
    ]
    assert result.findings[0].blocks == ["inferential_analysis"]
