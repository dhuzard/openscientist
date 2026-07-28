"""Versioned neutral schemas for preclinical context and assessment exchange."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class EvidenceValue(StrictContract):
    value: Any | None = None
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
