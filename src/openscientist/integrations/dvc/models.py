"""Typed contracts for DVC discovery and bounded imports."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DVCImportRequest(StrictModel):
    connection_id: str = Field(min_length=1, max_length=100)
    cage_ids: list[str] = Field(min_length=1, max_length=100)
    metric_id: str = Field(min_length=1, max_length=100)
    start: str = Field(min_length=10, max_length=40)
    stop: str = Field(min_length=10, max_length=40)
    aggregation: Literal["MINUTE", "HOUR"] = "MINUTE"

    @field_validator("cage_ids")
    @classmethod
    def unique_cages(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        if not cleaned:
            raise ValueError("At least one non-empty cage id is required.")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("Duplicate cage ids are not allowed.")
        return cleaned

    @field_validator("metric_id")
    @classmethod
    def one_metric(cls, value: str) -> str:
        value = value.strip().upper()
        if "," in value or " " in value:
            raise ValueError("Exactly one DVC metric id is allowed per import.")
        return value

    @model_validator(mode="after")
    def bounded_window(self) -> "DVCImportRequest":
        if self.start >= self.stop:
            raise ValueError("Import start must be earlier than import stop.")
        return self


class DVCAsset(StrictModel):
    asset_id: str
    role: Literal["raw_export", "normalized_measurements", "normalized_events", "manifest"]
    relative_path: str
    sha256: str
    bytes: int = Field(ge=0)


class DVCDatasetInspection(StrictModel):
    row_count: int = Field(ge=0)
    event_count: int = Field(ge=0)
    columns: list[str]
    event_columns: list[str]
    start_utc: str | None = None
    stop_utc: str | None = None
    warnings: list[str] = Field(default_factory=list)


class DVCDatasetResult(StrictModel):
    dataset_id: str
    connection_id: str
    metric_id: str
    cage_ids: list[str]
    aggregation: Literal["MINUTE", "HOUR"]
    assets: list[DVCAsset]
    inspection: DVCDatasetInspection
    vendor_state: dict[str, Any] = Field(default_factory=dict)
