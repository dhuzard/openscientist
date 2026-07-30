"""Typed contracts for DVC discovery and bounded imports."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

AssetRole = Literal[
    "raw_export",
    "normalized_measurements",
    "normalized_events",
    "manifest",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DVCImportRequest(StrictModel):
    connection_id: str = Field(min_length=1, max_length=100)
    cage_ids: list[str] = Field(min_length=1, max_length=100)
    metric_id: str = Field(min_length=1, max_length=100)
    start: str = Field(min_length=10, max_length=40)
    stop: str = Field(min_length=10, max_length=40)
    aggregation: Literal["MINUTE", "HOUR"] = "MINUTE"

    @field_validator("connection_id")
    @classmethod
    def valid_connection_id(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", value):
            raise ValueError(
                "connection_id must be 1-100 letters, numbers, dots, dashes or underscores."
            )
        return value

    @field_validator("cage_ids")
    @classmethod
    def unique_cages(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        if not cleaned:
            raise ValueError("At least one non-empty cage id is required.")
        if any(len(item) > 100 for item in cleaned):
            raise ValueError("Each cage id must be at most 100 characters.")
        uuid_pattern = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")
        if any(uuid_pattern.fullmatch(item) for item in cleaned):
            raise ValueError(
                "DVC imports require cage humanReadableId values (for example "
                "S81P-40332), not vendor UUIDs."
            )
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

    @field_validator("start", "stop")
    @classmethod
    def canonical_timestamp(cls, value: str) -> str:
        value = value.strip()
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Import bounds must be ISO-8601 timestamps.") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("Import bounds must include an explicit timezone offset.")
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    @model_validator(mode="after")
    def bounded_window(self) -> "DVCImportRequest":
        if self.start >= self.stop:
            raise ValueError("Import start must be earlier than import stop.")
        return self


class DVCAsset(StrictModel):
    asset_id: str
    role: AssetRole
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
    request_fingerprint: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    reused: bool = False
