"""Typed contracts for the Tecniplast DVC metadata-aware POC."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

T = TypeVar("T")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, str_strip_whitespace=True)


class ValueStatus(str, Enum):
    RECORDED = "recorded"
    COMPUTED = "computed"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


class ReviewStatus(str, Enum):
    UNREVIEWED = "unreviewed"
    APPROVED = "approved"
    REJECTED = "rejected"


class MetadataLevel(str, Enum):
    M0_MINIMAL = "M0_minimal"
    M1_UDWA = "M1_udwa"
    M2_MNMS_DVC = "M2_mnms_dvc"


class RequirementLevel(str, Enum):
    BLOCKING = "blocking"
    ANALYSIS_DEPENDENT = "analysis_dependent"
    RECOMMENDED = "recommended"
    DESCRIPTIVE = "descriptive"


class ExportType(str, Enum):
    TYPE1 = "type1_electrodes"
    TYPE2 = "type2_summary"
    EVENTS = "events"
    UNKNOWN = "unknown"


class WorkflowState(str, Enum):
    INGESTED = "ingested"
    STRUCTURE_RECONSTRUCTED = "structure_reconstructed"
    METADATA_VALIDATED = "metadata_validated"
    QC_COMPLETED = "qc_completed"
    PLAN_PROPOSED = "plan_proposed"
    PLAN_APPROVED = "plan_approved"
    ANALYSIS_EXECUTED = "analysis_executed"
    REPORT_REVIEWED = "report_reviewed"


class ContextValue(StrictModel, Generic[T]):
    """A value plus provenance, certainty and review state."""

    value: T | None = None
    status: ValueStatus = ValueStatus.UNKNOWN
    source: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    review_status: ReviewStatus = ReviewStatus.UNREVIEWED
    note: str | None = None

    @model_validator(mode="after")
    def validate_state(self) -> ContextValue[T]:
        if self.status is ValueStatus.UNKNOWN and self.value is not None:
            raise ValueError("unknown values cannot carry a value")
        if self.status is not ValueStatus.UNKNOWN and self.value is None:
            raise ValueError("known values must carry a value")
        if self.status is ValueStatus.INFERRED and self.confidence is None:
            raise ValueError("inferred values require confidence")
        return self

    @classmethod
    def recorded(
        cls,
        value: T,
        source: str,
        *,
        approved: bool = False,
        note: str | None = None,
    ) -> ContextValue[T]:
        return cls(
            value=value,
            status=ValueStatus.RECORDED,
            source=source,
            review_status=ReviewStatus.APPROVED if approved else ReviewStatus.UNREVIEWED,
            note=note,
        )

    @classmethod
    def computed(cls, value: T, source: str) -> ContextValue[T]:
        return cls(value=value, status=ValueStatus.COMPUTED, source=source)

    @classmethod
    def unknown(cls, note: str | None = None) -> ContextValue[T]:
        return cls(note=note)


class UnitDefinition(StrictModel):
    assignment_unit: ContextValue[str] = Field(default_factory=ContextValue[str].unknown)
    experimental_unit: ContextValue[str] = Field(default_factory=ContextValue[str].unknown)
    observation_unit: ContextValue[str] = Field(default_factory=ContextValue[str].unknown)
    analysis_unit: ContextValue[str] = Field(default_factory=ContextValue[str].unknown)


class CageContext(StrictModel):
    cage_id: str
    submitted_label: str
    export_group: ContextValue[str] = Field(default_factory=ContextValue[str].unknown)
    biological_group: ContextValue[str] = Field(default_factory=ContextValue[str].unknown)
    animals_per_cage: ContextValue[int] = Field(default_factory=ContextValue[int].unknown)


class AcquisitionContext(StrictModel):
    metric_name: ContextValue[str] = Field(default_factory=ContextValue[str].unknown)
    native_frequency_hz: ContextValue[float] = Field(default_factory=ContextValue[float].unknown)
    export_bin_seconds: ContextValue[float] = Field(default_factory=ContextValue[float].unknown)
    metric_definition: ContextValue[str] = Field(default_factory=ContextValue[str].unknown)
    software_version: ContextValue[str] = Field(default_factory=ContextValue[str].unknown)


class EnvironmentContext(StrictModel):
    source_utc_offset: ContextValue[str] = Field(default_factory=ContextValue[str].unknown)
    iana_timezone: ContextValue[str] = Field(default_factory=ContextValue[str].unknown)
    light_on: ContextValue[str] = Field(default_factory=ContextValue[str].unknown)
    light_off: ContextValue[str] = Field(default_factory=ContextValue[str].unknown)
    rem_source: ContextValue[str] = Field(default_factory=ContextValue[str].unknown)


class EventContext(StrictModel):
    event_code_definitions: ContextValue[dict[str, str]] = Field(
        default_factory=ContextValue[dict[str, str]].unknown
    )
    exclusion_policy: ContextValue[str] = Field(default_factory=ContextValue[str].unknown)
    origins_identified: ContextValue[bool] = Field(default_factory=ContextValue[bool].unknown)


class SourceAsset(StrictModel):
    asset_id: str
    filename: str
    role: Literal["type1", "type2", "events", "metadata", "rem", "other"]
    sha256: str | None = None


class StudyContext(StrictModel):
    schema_version: Literal["openscientist-dvc/0.1"] = "openscientist-dvc/0.1"
    metadata_level: MetadataLevel = MetadataLevel.M2_MNMS_DVC
    study_id: str
    title: ContextValue[str] = Field(default_factory=ContextValue[str].unknown)
    objective: ContextValue[str] = Field(default_factory=ContextValue[str].unknown)
    mode: Literal["exploratory", "confirmatory", "monitoring"] = "exploratory"
    units: UnitDefinition = Field(default_factory=UnitDefinition)
    cages: list[CageContext] = Field(default_factory=list)
    acquisition: AcquisitionContext = Field(default_factory=AcquisitionContext)
    environment: EnvironmentContext = Field(default_factory=EnvironmentContext)
    events: EventContext = Field(default_factory=EventContext)
    source_assets: list[SourceAsset] = Field(default_factory=list)
    unresolved_notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_cages(self) -> StudyContext:
        ids = [cage.cage_id for cage in self.cages]
        if len(ids) != len(set(ids)):
            raise ValueError("cage identifiers must be unique")
        return self


class ExportInspection(StrictModel):
    source_file: str
    export_type: ExportType
    row_count: int = Field(ge=0)
    cage_ids: list[str] = Field(default_factory=list)
    group_ids: list[str] = Field(default_factory=list)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    native_bin_seconds: float | None = Field(default=None, gt=0)
    warnings: list[str] = Field(default_factory=list)


class DVCSourceSpec(StrictModel):
    """Source-specific parsing and reconciliation contract for an upload."""

    path: Path
    source_id: str
    schema_hint: ExportType | None = None
    metric_name: str = "activity"
    site: str | None = None
    cohort: str | None = None
    iana_timezone: str | None = None
    clock_correction_minutes: int = 0
    clock_correction_reason: str = "none"
    expected_trace_count: int | None = Field(default=None, ge=1)
    expected_trace_ids: list[str] | None = None

    @model_validator(mode="after")
    def validate_expected_traces(self) -> DVCSourceSpec:
        if self.expected_trace_ids is not None:
            if len(self.expected_trace_ids) != len(set(self.expected_trace_ids)):
                raise ValueError("expected trace ids must be unique within a source")
            if self.expected_trace_count not in (None, len(self.expected_trace_ids)):
                raise ValueError("expected trace count differs from expected trace ids")
        if self.clock_correction_minutes and self.clock_correction_reason == "none":
            raise ValueError("a nonzero clock correction requires a reason")
        return self


class DVCImportSpec(StrictModel):
    schema_version: Literal["openscientist-dvc-upload/1"] = "openscientist-dvc-upload/1"
    sources: list[DVCSourceSpec] = Field(min_length=1)
    strict: bool = True


class DVCPreparedDataset(StrictModel):
    dataset_id: str
    cache_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_relpath: str
    measurement_asset_id: str
    reused: bool
    trace_count: int = Field(ge=1)
    row_count: int = Field(ge=1)


class AggregationValidation(StrictModel):
    compared_rows: int = Field(ge=0)
    matched_rows: int = Field(ge=0)
    unmatched_rows: int = Field(ge=0)
    max_absolute_difference: float | None = Field(default=None, ge=0)
    tolerance: float = Field(gt=0)


class GroupStatisticsValidation(StrictModel):
    compared_rows: int = Field(ge=0)
    average_matches: int = Field(ge=0)
    sem_matches_conventional: int = Field(ge=0)
    sem_matches_sample_sd: int = Field(ge=0)
    undefined_sem_rows: int = Field(ge=0)
    tolerance: float = Field(gt=0)


class MetadataGap(StrictModel):
    gap_id: str
    field_path: str
    level: RequirementLevel
    rationale: str
    blocks: list[str] = Field(default_factory=list)
    question: str
    priority: int = Field(ge=1, le=100)


class MetadataAssessment(StrictModel):
    gaps: list[MetadataGap]
    ready_for_qc: bool
    ready_for_descriptive_analysis: bool
    blocked_analyses: list[str]
    prioritized_questions: list[str]
    quality_score: float = Field(ge=0, le=100)


class AnalysisPlanStep(StrictModel):
    step_id: str
    title: str
    rationale: str
    tool_name: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    required_metadata: list[str] = Field(default_factory=list)
    approval_required: bool = False
    status: Literal["planned", "blocked", "approved", "executed"] = "planned"
    blocked_reason: str | None = None


class AnalysisPlan(StrictModel):
    objective: str
    scope: Literal["exploratory", "confirmatory", "monitoring"] = "exploratory"
    steps: list[AnalysisPlanStep]
    assumptions: list[str] = Field(default_factory=list)
    status: Literal["draft", "blocked", "approved", "executed"] = "draft"


class EvidenceRecord(StrictModel):
    evidence_id: str
    kind: Literal["source_data", "metadata", "computation", "agent_inference", "human_decision"]
    source: str
    description: str
    sha256: str | None = None
    payload: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ScientificClaim(StrictModel):
    claim_id: str
    text: str
    kind: Literal[
        "recorded_fact",
        "computed_result",
        "agent_inference",
        "unresolved_uncertainty",
        "human_conclusion",
    ]
    evidence_ids: list[str] = Field(min_length=1)
    review_status: ReviewStatus = ReviewStatus.UNREVIEWED
    limitations: list[str] = Field(default_factory=list)


class DecisionRecord(StrictModel):
    decision_id: str
    issue: str
    proposed_action: str
    rationale: str
    evidence_ids: list[str] = Field(default_factory=list)
    status: Literal["proposed", "approved", "rejected"] = "proposed"
    decided_by: str | None = None


class EvidenceLedger(StrictModel):
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    claims: list[ScientificClaim] = Field(default_factory=list)
    decisions: list[DecisionRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_references(self) -> EvidenceLedger:
        evidence_ids = {item.evidence_id for item in self.evidence}
        for claim in self.claims:
            missing = set(claim.evidence_ids) - evidence_ids
            if missing:
                raise ValueError(
                    f"claim {claim.claim_id} references missing evidence: {sorted(missing)}"
                )
        for decision in self.decisions:
            missing = set(decision.evidence_ids) - evidence_ids
            if missing:
                raise ValueError(
                    f"decision {decision.decision_id} references missing evidence: {sorted(missing)}"
                )
        return self
