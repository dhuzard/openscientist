"""Versioned neutral schemas for preclinical context and assessment exchange.

``PreclinicalStudyContext`` remains the stable 0.1 contract for existing DVC
callers. New assay implementations use ``PreclinicalStudyContextV2`` and can
upgrade or project contexts explicitly with ``from_v0_1`` and ``to_v0_1``.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from enum import StrEnum
from typing import Generic, Literal, TypeVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

T = TypeVar("T")


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EvidenceStatus(StrEnum):
    RECORDED = "recorded"
    COMPUTED = "computed"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


class AssessmentStatus(StrEnum):
    SATISFIED = "satisfied"
    PARTIAL = "partial"
    MISSING = "missing"
    NOT_APPLICABLE = "not_applicable"
    CONFLICTING = "conflicting"


class EvidenceValue(StrictContract, Generic[T]):
    """A value coupled to its epistemic status and provenance.

    The model remains valid when used without a type argument for compatibility
    with the 0.1 contract. New contracts should use ``EvidenceValue[T]`` so the
    scientific value is checked at both type-check and validation time.
    """

    value: T | None = None
    status: EvidenceStatus = EvidenceStatus.UNKNOWN
    source: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_epistemic_state(self) -> EvidenceValue:
        if self.status == EvidenceStatus.UNKNOWN and self.value is not None:
            raise ValueError("unknown values cannot carry a value")
        if self.status != EvidenceStatus.UNKNOWN and self.value is None:
            raise ValueError("known values must carry a value")
        if self.status == EvidenceStatus.INFERRED and self.confidence is None:
            raise ValueError("inferred values require confidence")
        return self


class StudyDesign(StrictContract):
    mode: Literal["exploratory", "confirmatory", "monitoring"] = "exploratory"
    assignment_unit: EvidenceValue = Field(default_factory=EvidenceValue)
    experimental_unit: EvidenceValue = Field(default_factory=EvidenceValue)
    observational_unit: EvidenceValue = Field(default_factory=EvidenceValue)
    analysis_unit: EvidenceValue = Field(default_factory=EvidenceValue)
    randomization: EvidenceValue = Field(default_factory=EvidenceValue)
    blinding: EvidenceValue = Field(default_factory=EvidenceValue)
    exclusion_policy: EvidenceValue = Field(default_factory=EvidenceValue)


class AnimalContext(StrictContract):
    species: EvidenceValue = Field(default_factory=EvidenceValue)
    strain: EvidenceValue = Field(default_factory=EvidenceValue)
    sex: EvidenceValue = Field(default_factory=EvidenceValue)
    age: EvidenceValue = Field(default_factory=EvidenceValue)
    occupancy: EvidenceValue = Field(default_factory=EvidenceValue)


class EnvironmentContext(StrictContract):
    timezone: EvidenceValue = Field(default_factory=EvidenceValue)
    light_schedule: EvidenceValue = Field(default_factory=EvidenceValue)
    housing: EvidenceValue = Field(default_factory=EvidenceValue)
    husbandry: EvidenceValue = Field(default_factory=EvidenceValue)


class AcquisitionContext(StrictContract):
    system: EvidenceValue = Field(default_factory=EvidenceValue)
    software_version: EvidenceValue = Field(default_factory=EvidenceValue)
    metric_contract: EvidenceValue = Field(default_factory=EvidenceValue)
    temporal_resolution: EvidenceValue = Field(default_factory=EvidenceValue)


class PreclinicalStudyContext(StrictContract):
    """The stable 0.1 aggregate context used by existing DVC integrations."""

    schema_version: Literal["openscientist-preclinical-context/0.1"] = (
        "openscientist-preclinical-context/0.1"
    )
    study_id: str
    objective: EvidenceValue = Field(default_factory=EvidenceValue)
    design: StudyDesign = Field(default_factory=StudyDesign)
    animals: AnimalContext = Field(default_factory=AnimalContext)
    environment: EnvironmentContext = Field(default_factory=EnvironmentContext)
    acquisition: AcquisitionContext = Field(default_factory=AcquisitionContext)
    asset_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class EntityType(StrEnum):
    STUDY = "study"
    COHORT = "cohort"
    SUBJECT = "subject"
    HOUSING_UNIT = "housing_unit"
    HOUSING_ASSIGNMENT = "housing_assignment"
    DEVICE = "device"
    SESSION = "session"
    OBSERVATION = "observation"
    INTERVENTION = "intervention"
    OUTCOME = "outcome"
    CUSTOM = "custom"


class EntityReference(StrictContract):
    entity_type: EntityType
    entity_id: str = Field(min_length=1)


class UnitDefinition(StrictContract):
    """Definition of one design unit, optionally bound to graph entities."""

    entity_type: EntityType
    label: str = Field(min_length=1)
    entity_ids: list[str] = Field(default_factory=list)
    legacy_value: JsonValue | None = None


class StudyUnitDefinitions(StrictContract):
    assignment: EvidenceValue[UnitDefinition] = Field(default_factory=EvidenceValue)
    experimental: EvidenceValue[UnitDefinition] = Field(default_factory=EvidenceValue)
    observational: EvidenceValue[UnitDefinition] = Field(default_factory=EvidenceValue)
    analysis: EvidenceValue[UnitDefinition] = Field(default_factory=EvidenceValue)


class RepeatedMeasureDefinition(StrictContract):
    repeated_unit: UnitDefinition
    within_unit_factors: list[str] = Field(default_factory=list)
    expected_count: int | None = Field(default=None, ge=1)
    interval_seconds: float | None = Field(default=None, gt=0)


class StudyDesignV2(StrictContract):
    mode: Literal["exploratory", "confirmatory", "monitoring"] = "exploratory"
    units: StudyUnitDefinitions = Field(default_factory=StudyUnitDefinitions)
    repeated_measures: list[EvidenceValue[RepeatedMeasureDefinition]] = Field(default_factory=list)
    randomization: EvidenceValue[JsonValue] = Field(default_factory=EvidenceValue)
    blinding: EvidenceValue[JsonValue] = Field(default_factory=EvidenceValue)
    exclusion_policy: EvidenceValue[JsonValue] = Field(default_factory=EvidenceValue)


class StudyDefaults(StrictContract):
    """Study-level defaults retained when migrating a 0.1 aggregate context."""

    species: EvidenceValue[JsonValue] = Field(default_factory=EvidenceValue)
    strain: EvidenceValue[JsonValue] = Field(default_factory=EvidenceValue)
    sex: EvidenceValue[JsonValue] = Field(default_factory=EvidenceValue)
    age: EvidenceValue[JsonValue] = Field(default_factory=EvidenceValue)
    occupancy: EvidenceValue[JsonValue] = Field(default_factory=EvidenceValue)
    timezone: EvidenceValue[str] = Field(default_factory=EvidenceValue)
    light_schedule: EvidenceValue[JsonValue] = Field(default_factory=EvidenceValue)
    housing: EvidenceValue[JsonValue] = Field(default_factory=EvidenceValue)
    husbandry: EvidenceValue[JsonValue] = Field(default_factory=EvidenceValue)
    acquisition_system: EvidenceValue[JsonValue] = Field(default_factory=EvidenceValue)
    software_version: EvidenceValue[JsonValue] = Field(default_factory=EvidenceValue)
    metric_contract: EvidenceValue[JsonValue] = Field(default_factory=EvidenceValue)
    temporal_resolution: EvidenceValue[JsonValue] = Field(default_factory=EvidenceValue)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: EvidenceValue[str]) -> EvidenceValue[str]:
        _validate_timezone(value)
        return value


class StudyNode(StrictContract):
    study_id: str = Field(min_length=1)
    objective: EvidenceValue[JsonValue] = Field(default_factory=EvidenceValue)


class CohortNode(StrictContract):
    cohort_id: str = Field(min_length=1)
    study_id: str = Field(min_length=1)
    label: EvidenceValue[str] = Field(default_factory=EvidenceValue)


class SubjectNode(StrictContract):
    subject_id: str = Field(min_length=1)
    cohort_id: str = Field(min_length=1)
    species: EvidenceValue[str] = Field(default_factory=EvidenceValue)
    strain: EvidenceValue[str] = Field(default_factory=EvidenceValue)
    sex: EvidenceValue[str] = Field(default_factory=EvidenceValue)
    birth_date: EvidenceValue[date] = Field(default_factory=EvidenceValue)


class HousingUnitNode(StrictContract):
    housing_unit_id: str = Field(min_length=1)
    study_id: str = Field(min_length=1)
    label: EvidenceValue[str] = Field(default_factory=EvidenceValue)
    housing_type: EvidenceValue[str] = Field(default_factory=EvidenceValue)


class HousingAssignment(StrictContract):
    assignment_id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    housing_unit_id: str = Field(min_length=1)
    starts_at: EvidenceValue[datetime] = Field(default_factory=EvidenceValue)
    ends_at: EvidenceValue[datetime] = Field(default_factory=EvidenceValue)

    @field_validator("starts_at", "ends_at")
    @classmethod
    def validate_timestamp(cls, value: EvidenceValue[datetime]) -> EvidenceValue[datetime]:
        _validate_aware_timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_interval(self) -> HousingAssignment:
        if self.starts_at.value is not None and self.ends_at.value is not None:
            if self.ends_at.value <= self.starts_at.value:
                raise ValueError("housing assignment ends_at must follow starts_at")
        return self


class DeviceNode(StrictContract):
    device_id: str = Field(min_length=1)
    study_id: str = Field(min_length=1)
    housing_unit_id: str | None = None
    system: EvidenceValue[str] = Field(default_factory=EvidenceValue)
    model: EvidenceValue[str] = Field(default_factory=EvidenceValue)
    serial_number: EvidenceValue[str] = Field(default_factory=EvidenceValue)
    software_version: EvidenceValue[str] = Field(default_factory=EvidenceValue)


class ClockSynchronization(StrictContract):
    reference_device_id: str | None = None
    method: EvidenceValue[str] = Field(default_factory=EvidenceValue)
    offset_seconds: EvidenceValue[float] = Field(default_factory=EvidenceValue)
    uncertainty_seconds: EvidenceValue[float] = Field(default_factory=EvidenceValue)
    synchronized_at: EvidenceValue[datetime] = Field(default_factory=EvidenceValue)

    @field_validator("synchronized_at")
    @classmethod
    def validate_timestamp(cls, value: EvidenceValue[datetime]) -> EvidenceValue[datetime]:
        _validate_aware_timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_uncertainty(self) -> ClockSynchronization:
        if self.uncertainty_seconds.value is not None and self.uncertainty_seconds.value < 0:
            raise ValueError("clock synchronization uncertainty cannot be negative")
        return self


class SessionNode(StrictContract):
    session_id: str = Field(min_length=1)
    study_id: str = Field(min_length=1)
    subject_ids: list[str] = Field(default_factory=list)
    housing_unit_ids: list[str] = Field(default_factory=list)
    device_ids: list[str] = Field(default_factory=list)
    starts_at: EvidenceValue[datetime] = Field(default_factory=EvidenceValue)
    ends_at: EvidenceValue[datetime] = Field(default_factory=EvidenceValue)
    timezone: EvidenceValue[str] = Field(default_factory=EvidenceValue)
    clock_synchronization: ClockSynchronization = Field(default_factory=ClockSynchronization)

    @field_validator("starts_at", "ends_at")
    @classmethod
    def validate_timestamp(cls, value: EvidenceValue[datetime]) -> EvidenceValue[datetime]:
        _validate_aware_timestamp(value)
        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: EvidenceValue[str]) -> EvidenceValue[str]:
        _validate_timezone(value)
        return value

    @model_validator(mode="after")
    def validate_interval(self) -> SessionNode:
        if self.starts_at.value is not None and self.ends_at.value is not None:
            if self.ends_at.value <= self.starts_at.value:
                raise ValueError("session ends_at must follow starts_at")
        return self


class InterventionNode(StrictContract):
    intervention_id: str = Field(min_length=1)
    study_id: str = Field(min_length=1)
    targets: list[EntityReference] = Field(min_length=1)
    intervention_type: EvidenceValue[str] = Field(default_factory=EvidenceValue)
    description: EvidenceValue[str] = Field(default_factory=EvidenceValue)
    starts_at: EvidenceValue[datetime] = Field(default_factory=EvidenceValue)
    ends_at: EvidenceValue[datetime] = Field(default_factory=EvidenceValue)

    @field_validator("starts_at", "ends_at")
    @classmethod
    def validate_timestamp(cls, value: EvidenceValue[datetime]) -> EvidenceValue[datetime]:
        _validate_aware_timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_interval(self) -> InterventionNode:
        if self.starts_at.value is not None and self.ends_at.value is not None:
            if self.ends_at.value <= self.starts_at.value:
                raise ValueError("intervention ends_at must follow starts_at")
        return self


class OutcomeNode(StrictContract):
    outcome_id: str = Field(min_length=1)
    study_id: str = Field(min_length=1)
    name: EvidenceValue[str]
    unit: EvidenceValue[str] = Field(default_factory=EvidenceValue)
    description: EvidenceValue[str] = Field(default_factory=EvidenceValue)


class ObservationNode(StrictContract):
    observation_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    outcome_id: str = Field(min_length=1)
    observed_units: list[EntityReference] = Field(min_length=1)
    observed_at: EvidenceValue[datetime] = Field(default_factory=EvidenceValue)
    value: EvidenceValue[JsonValue]

    @field_validator("observed_at")
    @classmethod
    def validate_timestamp(cls, value: EvidenceValue[datetime]) -> EvidenceValue[datetime]:
        _validate_aware_timestamp(value)
        return value


def _validate_timezone(value: EvidenceValue[str]) -> None:
    if value.value is not None:
        try:
            ZoneInfo(value.value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown IANA timezone: {value.value}") from exc


def _validate_aware_timestamp(value: EvidenceValue[datetime]) -> None:
    if value.value is not None and value.value.utcoffset() is None:
        raise ValueError("timestamps must include a UTC offset")


def _copy_evidence(value: EvidenceValue[object]) -> EvidenceValue[JsonValue]:
    return EvidenceValue[JsonValue].model_validate(value.model_dump(mode="json"))


def _copy_string_evidence(value: EvidenceValue[object]) -> EvidenceValue[str]:
    payload = value.model_dump(mode="json")
    if payload["value"] is not None and not isinstance(payload["value"], str):
        payload["value"] = json.dumps(payload["value"], sort_keys=True, separators=(",", ":"))
    return EvidenceValue[str].model_validate(payload)


def _legacy_unit(value: EvidenceValue[object]) -> EvidenceValue[UnitDefinition]:
    if value.value is None:
        return EvidenceValue[UnitDefinition].model_validate(value.model_dump(mode="json"))
    serialized = value.model_dump(mode="json")["value"]
    label = serialized if isinstance(serialized, str) else json.dumps(serialized, sort_keys=True)
    normalized = label.lower().replace("-", "_").replace(" ", "_")
    entity_type = {
        "study": EntityType.STUDY,
        "cohort": EntityType.COHORT,
        "animal": EntityType.SUBJECT,
        "mouse": EntityType.SUBJECT,
        "subject": EntityType.SUBJECT,
        "cage": EntityType.HOUSING_UNIT,
        "housing_unit": EntityType.HOUSING_UNIT,
        "device": EntityType.DEVICE,
        "session": EntityType.SESSION,
        "observation": EntityType.OBSERVATION,
    }.get(normalized, EntityType.CUSTOM)
    return EvidenceValue[UnitDefinition](
        value=UnitDefinition(
            entity_type=entity_type,
            label=label,
            legacy_value=serialized,
        ),
        status=value.status,
        source=value.source,
        confidence=value.confidence,
    )


class PreclinicalStudyContextV2(StrictContract):
    """Typed, validated experimental graph for heterogeneous preclinical assays."""

    schema_version: Literal["openscientist-preclinical-context/0.2"] = (
        "openscientist-preclinical-context/0.2"
    )
    study: StudyNode
    design: StudyDesignV2 = Field(default_factory=StudyDesignV2)
    defaults: StudyDefaults = Field(default_factory=StudyDefaults)
    cohorts: list[CohortNode] = Field(default_factory=list)
    subjects: list[SubjectNode] = Field(default_factory=list)
    housing_units: list[HousingUnitNode] = Field(default_factory=list)
    housing_assignments: list[HousingAssignment] = Field(default_factory=list)
    devices: list[DeviceNode] = Field(default_factory=list)
    sessions: list[SessionNode] = Field(default_factory=list)
    interventions: list[InterventionNode] = Field(default_factory=list)
    outcomes: list[OutcomeNode] = Field(default_factory=list)
    observations: list[ObservationNode] = Field(default_factory=list)
    asset_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)

    @property
    def study_id(self) -> str:
        """Compatibility accessor shared with the 0.1 context."""

        return self.study.study_id

    @model_validator(mode="after")
    def validate_graph(self) -> PreclinicalStudyContextV2:
        entity_sets: dict[EntityType, set[str]] = {
            EntityType.STUDY: {self.study.study_id},
            EntityType.COHORT: {item.cohort_id for item in self.cohorts},
            EntityType.SUBJECT: {item.subject_id for item in self.subjects},
            EntityType.HOUSING_UNIT: {item.housing_unit_id for item in self.housing_units},
            EntityType.HOUSING_ASSIGNMENT: {
                item.assignment_id for item in self.housing_assignments
            },
            EntityType.DEVICE: {item.device_id for item in self.devices},
            EntityType.SESSION: {item.session_id for item in self.sessions},
            EntityType.INTERVENTION: {item.intervention_id for item in self.interventions},
            EntityType.OUTCOME: {item.outcome_id for item in self.outcomes},
            EntityType.OBSERVATION: {item.observation_id for item in self.observations},
        }
        all_ids = [entity_id for ids in entity_sets.values() for entity_id in ids]
        expected_count = (
            1
            + len(self.cohorts)
            + len(self.subjects)
            + len(self.housing_units)
            + len(self.housing_assignments)
            + len(self.devices)
            + len(self.sessions)
            + len(self.interventions)
            + len(self.outcomes)
            + len(self.observations)
        )
        if len(all_ids) != expected_count or len(set(all_ids)) != expected_count:
            raise ValueError("entity identifiers must be globally unique")

        def require(entity_type: EntityType, entity_id: str, relation: str) -> None:
            if entity_type not in entity_sets:
                raise ValueError(
                    f"{relation} uses non-addressable entity type {entity_type.value!r}"
                )
            if entity_id not in entity_sets[entity_type]:
                raise ValueError(f"{relation} references missing {entity_type.value} {entity_id!r}")

        for cohort in self.cohorts:
            require(EntityType.STUDY, cohort.study_id, f"cohort {cohort.cohort_id!r}")
        for subject in self.subjects:
            require(EntityType.COHORT, subject.cohort_id, f"subject {subject.subject_id!r}")
        for housing in self.housing_units:
            require(EntityType.STUDY, housing.study_id, f"housing unit {housing.housing_unit_id!r}")
        for assignment in self.housing_assignments:
            require(
                EntityType.SUBJECT,
                assignment.subject_id,
                f"assignment {assignment.assignment_id!r}",
            )
            require(
                EntityType.HOUSING_UNIT,
                assignment.housing_unit_id,
                f"assignment {assignment.assignment_id!r}",
            )
        for device in self.devices:
            require(EntityType.STUDY, device.study_id, f"device {device.device_id!r}")
            if device.housing_unit_id is not None:
                require(
                    EntityType.HOUSING_UNIT,
                    device.housing_unit_id,
                    f"device {device.device_id!r}",
                )
        for session in self.sessions:
            require(EntityType.STUDY, session.study_id, f"session {session.session_id!r}")
            for relation_name, references in (
                ("subject_ids", session.subject_ids),
                ("housing_unit_ids", session.housing_unit_ids),
                ("device_ids", session.device_ids),
            ):
                if len(references) != len(set(references)):
                    raise ValueError(
                        f"session {session.session_id!r} contains duplicate {relation_name}"
                    )
            for subject_id in session.subject_ids:
                require(EntityType.SUBJECT, subject_id, f"session {session.session_id!r}")
            for housing_unit_id in session.housing_unit_ids:
                require(
                    EntityType.HOUSING_UNIT,
                    housing_unit_id,
                    f"session {session.session_id!r}",
                )
            for device_id in session.device_ids:
                require(EntityType.DEVICE, device_id, f"session {session.session_id!r}")
            reference = session.clock_synchronization.reference_device_id
            if reference is not None:
                require(EntityType.DEVICE, reference, f"session {session.session_id!r} clock")
        for intervention in self.interventions:
            require(
                EntityType.STUDY,
                intervention.study_id,
                f"intervention {intervention.intervention_id!r}",
            )
            target_keys = [
                (target.entity_type, target.entity_id) for target in intervention.targets
            ]
            if len(target_keys) != len(set(target_keys)):
                raise ValueError(
                    f"intervention {intervention.intervention_id!r} contains duplicate targets"
                )
            for target in intervention.targets:
                require(
                    target.entity_type,
                    target.entity_id,
                    f"intervention {intervention.intervention_id!r}",
                )
        for outcome in self.outcomes:
            require(EntityType.STUDY, outcome.study_id, f"outcome {outcome.outcome_id!r}")
        for observation in self.observations:
            require(
                EntityType.SESSION,
                observation.session_id,
                f"observation {observation.observation_id!r}",
            )
            require(
                EntityType.OUTCOME,
                observation.outcome_id,
                f"observation {observation.observation_id!r}",
            )
            observed_keys = [
                (observed_unit.entity_type, observed_unit.entity_id)
                for observed_unit in observation.observed_units
            ]
            if len(observed_keys) != len(set(observed_keys)):
                raise ValueError(
                    f"observation {observation.observation_id!r} contains duplicate observed units"
                )
            for observed_unit in observation.observed_units:
                require(
                    observed_unit.entity_type,
                    observed_unit.entity_id,
                    f"observation {observation.observation_id!r}",
                )
        for unit_name, evidence in (
            ("assignment", self.design.units.assignment),
            ("experimental", self.design.units.experimental),
            ("observational", self.design.units.observational),
            ("analysis", self.design.units.analysis),
        ):
            if evidence.value is not None:
                if len(evidence.value.entity_ids) != len(set(evidence.value.entity_ids)):
                    raise ValueError(f"{unit_name} unit contains duplicate entity_ids")
                for entity_id in evidence.value.entity_ids:
                    require(evidence.value.entity_type, entity_id, f"{unit_name} unit")
        for index, repeated_evidence in enumerate(self.design.repeated_measures):
            if repeated_evidence.value is not None:
                unit = repeated_evidence.value.repeated_unit
                if len(unit.entity_ids) != len(set(unit.entity_ids)):
                    raise ValueError(f"repeated measure {index} contains duplicate entity_ids")
                for entity_id in unit.entity_ids:
                    require(unit.entity_type, entity_id, f"repeated measure {index}")
        return self

    @classmethod
    def from_v0_1(cls, context: PreclinicalStudyContext) -> PreclinicalStudyContextV2:
        """Losslessly project a 0.1 aggregate context into the 0.2 graph."""

        return cls(
            study=StudyNode(
                study_id=context.study_id,
                objective=_copy_evidence(context.objective),
            ),
            design=StudyDesignV2(
                mode=context.design.mode,
                units=StudyUnitDefinitions(
                    assignment=_legacy_unit(context.design.assignment_unit),
                    experimental=_legacy_unit(context.design.experimental_unit),
                    observational=_legacy_unit(context.design.observational_unit),
                    analysis=_legacy_unit(context.design.analysis_unit),
                ),
                randomization=_copy_evidence(context.design.randomization),
                blinding=_copy_evidence(context.design.blinding),
                exclusion_policy=_copy_evidence(context.design.exclusion_policy),
            ),
            defaults=StudyDefaults(
                species=_copy_evidence(context.animals.species),
                strain=_copy_evidence(context.animals.strain),
                sex=_copy_evidence(context.animals.sex),
                age=_copy_evidence(context.animals.age),
                occupancy=_copy_evidence(context.animals.occupancy),
                timezone=_copy_string_evidence(context.environment.timezone),
                light_schedule=_copy_evidence(context.environment.light_schedule),
                housing=_copy_evidence(context.environment.housing),
                husbandry=_copy_evidence(context.environment.husbandry),
                acquisition_system=_copy_evidence(context.acquisition.system),
                software_version=_copy_evidence(context.acquisition.software_version),
                metric_contract=_copy_evidence(context.acquisition.metric_contract),
                temporal_resolution=_copy_evidence(context.acquisition.temporal_resolution),
            ),
            asset_ids=context.asset_ids,
            evidence_ids=context.evidence_ids,
        )

    def to_v0_1(self) -> PreclinicalStudyContext:
        """Project 0.2 study-level fields to the stable 0.1 DVC contract."""

        def legacy_unit(value: EvidenceValue[UnitDefinition]) -> EvidenceValue[object]:
            payload = value.model_dump(mode="json")
            unit = value.value
            payload["value"] = (
                None
                if unit is None
                else unit.legacy_value
                if unit.legacy_value is not None
                else unit.label
            )
            return EvidenceValue[object].model_validate(payload)

        return PreclinicalStudyContext(
            study_id=self.study.study_id,
            objective=EvidenceValue.model_validate(self.study.objective.model_dump(mode="json")),
            design=StudyDesign(
                mode=self.design.mode,
                assignment_unit=legacy_unit(self.design.units.assignment),
                experimental_unit=legacy_unit(self.design.units.experimental),
                observational_unit=legacy_unit(self.design.units.observational),
                analysis_unit=legacy_unit(self.design.units.analysis),
                randomization=EvidenceValue.model_validate(
                    self.design.randomization.model_dump(mode="json")
                ),
                blinding=EvidenceValue.model_validate(self.design.blinding.model_dump(mode="json")),
                exclusion_policy=EvidenceValue.model_validate(
                    self.design.exclusion_policy.model_dump(mode="json")
                ),
            ),
            animals=AnimalContext(
                species=EvidenceValue.model_validate(self.defaults.species.model_dump(mode="json")),
                strain=EvidenceValue.model_validate(self.defaults.strain.model_dump(mode="json")),
                sex=EvidenceValue.model_validate(self.defaults.sex.model_dump(mode="json")),
                age=EvidenceValue.model_validate(self.defaults.age.model_dump(mode="json")),
                occupancy=EvidenceValue.model_validate(
                    self.defaults.occupancy.model_dump(mode="json")
                ),
            ),
            environment=EnvironmentContext(
                timezone=EvidenceValue.model_validate(
                    self.defaults.timezone.model_dump(mode="json")
                ),
                light_schedule=EvidenceValue.model_validate(
                    self.defaults.light_schedule.model_dump(mode="json")
                ),
                housing=EvidenceValue.model_validate(self.defaults.housing.model_dump(mode="json")),
                husbandry=EvidenceValue.model_validate(
                    self.defaults.husbandry.model_dump(mode="json")
                ),
            ),
            acquisition=AcquisitionContext(
                system=EvidenceValue.model_validate(
                    self.defaults.acquisition_system.model_dump(mode="json")
                ),
                software_version=EvidenceValue.model_validate(
                    self.defaults.software_version.model_dump(mode="json")
                ),
                metric_contract=EvidenceValue.model_validate(
                    self.defaults.metric_contract.model_dump(mode="json")
                ),
                temporal_resolution=EvidenceValue.model_validate(
                    self.defaults.temporal_resolution.model_dump(mode="json")
                ),
            ),
            asset_ids=self.asset_ids,
            evidence_ids=self.evidence_ids,
        )


class AssessmentFinding(StrictContract):
    requirement_id: str
    status: AssessmentStatus
    evidence_ids: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    blocks: list[str] = Field(default_factory=list)
    recommendation: str | None = None


class AssessmentResult(StrictContract):
    schema_version: Literal["openscientist-preclinical-assessment/0.1"] = (
        "openscientist-preclinical-assessment/0.1"
    )
    assessment_id: str
    framework: str
    framework_version: str
    context_hash: str
    findings: list[AssessmentFinding]
