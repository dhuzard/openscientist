"""Strict public request and result models for derived open-field tracking."""

from __future__ import annotations

from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DATASET_ID_PATTERN = r"^open-field-[0-9a-f]{24}$"


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )


class OpenFieldImportMetadata(_StrictModel):
    """Required acquisition and unit semantics; none are inferred from a CSV."""

    frame_rate_hz: float = Field(gt=0, le=10_000)
    frame_rate_tolerance_fraction: float = Field(default=0.05, gt=0, le=0.5)
    timezone: str = Field(min_length=1, max_length=100)
    clock_id: str = Field(min_length=1, max_length=200)
    clock_synchronized: bool
    coordinate_unit: Literal["mm", "cm", "m", "pixel"]
    timestamp_unit: Literal["seconds"]
    experimental_unit: Literal["subject"]
    observational_unit: Literal["subject_session"]
    analysis_unit: Literal["subject"]

    @field_validator("timezone")
    @classmethod
    def timezone_must_be_iana(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value


class OpenFieldImportRequest(_StrictModel):
    """Import one job-local derived tracking CSV."""

    source_relative_path: str = Field(min_length=1, max_length=1000)
    metadata: OpenFieldImportMetadata

    @field_validator("source_relative_path")
    @classmethod
    def source_must_be_relative(cls, value: str) -> str:
        normalized = value.replace("\\", "/")
        if normalized.startswith("/") or ".." in normalized.split("/"):
            raise ValueError("source_relative_path must remain inside the job directory")
        return normalized


class OpenFieldAnalysisRequest(_StrictModel):
    """One analysis bound to subject-level inference."""

    dataset_id: str = Field(pattern=DATASET_ID_PATTERN)
    study_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
    context_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parameters_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    analysis_unit: Literal["subject"]


class OpenFieldDatasetResult(_StrictModel):
    dataset_id: str = Field(pattern=DATASET_ID_PATTERN)
    normalized_relative_path: str
    manifest_relative_path: str
    normalized_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    row_count: int = Field(gt=0)
    subject_count: int = Field(gt=0)
    session_count: int = Field(gt=0)
    metadata: OpenFieldImportMetadata


class OpenFieldAnalysisResult(_StrictModel):
    schema_id: Literal["openscientist-open-field-analysis/1.0"] = (
        "openscientist-open-field-analysis/1.0"
    )
    dataset_id: str = Field(pattern=DATASET_ID_PATTERN)
    run_id: str
    operation_id: Literal["check_data_sanity", "summarize_distance", "summarize_zone_occupancy"]
    analysis_unit: Literal["subject"]
    passed: bool
    result: dict[str, Any]
    result_relative_path: str
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provenance_relative_path: str
    provenance_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def paths_are_relative(self) -> "OpenFieldAnalysisResult":
        for field, value in (
            ("result_relative_path", self.result_relative_path),
            ("provenance_relative_path", self.provenance_relative_path),
        ):
            normalized = value.replace("\\", "/")
            if normalized.startswith("/") or ".." in normalized.split("/"):
                raise ValueError(f"{field} must remain inside the job directory")
        return self
