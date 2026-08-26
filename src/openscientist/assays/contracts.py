"""Typed contracts for governed preclinical assay integrations."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ApprovalDecision(StrictModel):
    """A human decision bound to one exact analysis run and contract."""

    approval_id: str = Field(min_length=1, max_length=200)
    run_id: str = Field(min_length=1, max_length=200)
    assay_id: str = Field(min_length=1, max_length=100)
    dataset_id: str = Field(min_length=1, max_length=200)
    operation_id: str = Field(min_length=1, max_length=100)
    contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parameters_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    decided_by: str = Field(min_length=1, max_length=200)
    decided_at: datetime
    decision: Literal["approved", "rejected", "revoked"]
    rationale: str | None = Field(default=None, max_length=4000)

    @field_validator("decided_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Approval decision timestamp must include a timezone.")
        return value


class EvidenceArtifact(StrictModel):
    """Content-addressed evidence produced or consumed by one analysis run."""

    artifact_id: str = Field(min_length=1, max_length=200)
    run_id: str = Field(min_length=1, max_length=200)
    assay_id: str = Field(min_length=1, max_length=100)
    dataset_id: str = Field(min_length=1, max_length=200)
    role: str = Field(min_length=1, max_length=100)
    relative_path: str = Field(min_length=1, max_length=1000)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(ge=0)
    media_type: str | None = Field(default=None, max_length=200)
    schema_id: str | None = Field(default=None, max_length=300)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("relative_path")
    @classmethod
    def safe_relative_path(cls, value: str) -> str:
        normalized = value.replace("\\", "/")
        if normalized.startswith("/") or ".." in normalized.split("/"):
            raise ValueError("Evidence artifact paths must remain relative to the job directory.")
        return normalized

    @field_validator("created_at")
    @classmethod
    def created_at_timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Evidence artifact timestamp must include a timezone.")
        return value


class ValidationFinding(StrictModel):
    check_id: str = Field(min_length=1, max_length=200)
    passed: bool
    message: str = Field(min_length=1, max_length=2000)
    observed: Any = None


class ValidationResult(StrictModel):
    validator_id: str = Field(min_length=1, max_length=200)
    validator_version: str = Field(min_length=1, max_length=100)
    passed: bool
    findings: list[ValidationFinding] = Field(default_factory=list)

    @model_validator(mode="after")
    def result_matches_findings(self) -> "ValidationResult":
        if self.passed != all(item.passed for item in self.findings):
            raise ValueError("Validation result status must match all finding statuses.")
        return self


ValidatorCallable = Callable[[Mapping[str, Any]], ValidationResult]


@dataclass(frozen=True)
class ExecutableValidator:
    """A versioned, executable fail-closed validator registered by an assay."""

    validator_id: str
    version: str
    validate: ValidatorCallable = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.validator_id.strip() or not self.version.strip():
            raise ValueError("Executable validators require non-empty identity and version.")

    def __call__(self, payload: Mapping[str, Any]) -> ValidationResult:
        result = self.validate(payload)
        if result.validator_id != self.validator_id or result.validator_version != self.version:
            raise ValueError("Validator result identity does not match its registration.")
        return result


@dataclass(frozen=True)
class OperationContract:
    """Versioned governance and evidence contract for one assay operation."""

    contract_version: str
    input_roles: tuple[str, ...]
    required_context: tuple[str, ...]
    approval_required: bool
    output_evidence: tuple[str, ...]
    numerical_tolerance: str
    operation_id: str = "unbound_operation"
    display_name: str = "Unbound operation"
    allowed_modes: tuple[str, ...] = ("exploratory", "confirmatory", "monitoring")
    vendor_equivalence: Literal["not_claimed", "validated"] = "not_claimed"
    conformance_fixture: str | None = None
    validator_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{1,99}", self.operation_id):
            raise ValueError("Operation ids must be stable lowercase identifiers.")
        if not self.contract_version or not self.display_name:
            raise ValueError("Operation contracts require a display name and version.")
        if self.vendor_equivalence == "validated" and not self.conformance_fixture:
            raise ValueError("Vendor-equivalent contracts require a conformance fixture.")
        if len(set(self.validator_ids)) != len(self.validator_ids):
            raise ValueError("Operation validator ids must be unique.")


@dataclass(frozen=True)
class EvidencePattern:
    role: str
    glob: str
    required: bool = True
    schema_id: str | None = None


@dataclass(frozen=True)
class GatewayAction:
    action: str
    permission: str
    handler_path: str
    request_model_path: str
    service_method: str
    mutating: bool
    requires_connection: bool = False
    invocation: Literal["model", "kwargs"] = "model"


@dataclass(frozen=True)
class ReviewPanelSpec:
    panel_id: str
    title: str
    summary_fields: tuple[str, ...]
    decision_fields: tuple[str, ...] = ("decision", "rationale")
    checkpoint_globs: tuple[str, ...] = ()
    approval_create_handler_path: str | None = None
    approval_list_handler_path: str | None = None
    context_loader_handler_path: str | None = None


@dataclass(frozen=True)
class AssayAdapter:
    """Immutable descriptor consumed by gateways, packagers, workflows, and UI."""

    adapter_id: str
    display_name: str
    adapter_version: str
    dataset_id_pattern: str
    operation_contracts: Mapping[str, OperationContract]
    evidence_patterns: tuple[EvidencePattern, ...]
    manifest_schemas: tuple[str, ...]
    gateway_actions: tuple[GatewayAction, ...]
    review_panel: ReviewPanelSpec
    validators: Mapping[str, ExecutableValidator] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{1,99}", self.adapter_id):
            raise ValueError("Assay adapter ids must be stable lowercase identifiers.")
        re.compile(self.dataset_id_pattern)
        for operation_id, contract in self.operation_contracts.items():
            if operation_id != contract.operation_id:
                raise ValueError("Operation contract keys must equal operation_id.")
            missing = set(contract.validator_ids) - set(self.validators)
            if missing:
                raise ValueError(
                    f"Operation '{operation_id}' references unregistered validators: {sorted(missing)}"
                )

    def require_operation(self, operation_id: str) -> OperationContract:
        try:
            return self.operation_contracts[operation_id]
        except KeyError as exc:
            raise KeyError(
                f"Operation '{operation_id}' is not registered for assay '{self.adapter_id}'."
            ) from exc

    def validate_dataset_id(self, dataset_id: str) -> bool:
        return re.fullmatch(self.dataset_id_pattern, dataset_id) is not None
