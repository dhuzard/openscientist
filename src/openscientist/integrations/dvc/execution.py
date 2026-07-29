"""Governed deterministic UDWA execution for imported DVC datasets."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openscientist.integrations.dvc.security import (
    contains_sensitive_key,
    redact_sensitive_data,
    redact_sensitive_text,
)
from openscientist.integrations.udwa import inspect_udwa_compatibility
from openscientist.preclinical_context.models import EvidenceStatus, PreclinicalStudyContext

AnalysisAssetRole = Literal["result", "provenance"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DVCAnalysisApproval(StrictModel):
    """Human approval bound to one exact governed analysis request."""

    approval_id: str = Field(min_length=1, max_length=200)
    approved_by: str = Field(min_length=1, max_length=200)
    approved_at: datetime
    dataset_id: str = Field(pattern=r"^dvc-[0-9a-fA-F-]{36}$")
    pre_analysis_checkpoint_id: str = Field(pattern=r"^dvc-assess-[0-9a-fA-F-]{36}$")
    pre_analysis_checkpoint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    operation: str = Field(min_length=1, max_length=100)
    context_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parameters_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["approved"] = "approved"

    @field_validator("approved_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Approval timestamp must include a timezone.")
        return value


class DVCAnalysisRequest(StrictModel):
    dataset_id: str = Field(pattern=r"^dvc-[0-9a-fA-F-]{36}$")
    pre_analysis_checkpoint_id: str = Field(pattern=r"^dvc-assess-[0-9a-fA-F-]{36}$")
    operation: str = Field(min_length=1, max_length=100)
    context: PreclinicalStudyContext
    parameters: dict[str, Any] = Field(default_factory=dict)
    approval: DVCAnalysisApproval | None = None

    @field_validator("parameters")
    @classmethod
    def reject_credential_parameters(cls, value: dict[str, Any]) -> dict[str, Any]:
        if contains_sensitive_key(value):
            raise ValueError("Analysis parameters must not contain credentials.")
        canonical_parameters_sha256(value)
        return value

    @model_validator(mode="after")
    def approval_targets_operation(self) -> "DVCAnalysisRequest":
        if self.approval and self.approval.operation != self.operation:
            raise ValueError("Approval operation does not match the requested operation.")
        return self


class DVCAnalysisAsset(StrictModel):
    role: AnalysisAssetRole
    relative_path: str
    sha256: str
    bytes: int = Field(ge=0)


class DVCAnalysisResult(StrictModel):
    execution_id: str
    dataset_id: str
    operation: str
    status: Literal["completed"] = "completed"
    specialist: str | None = None
    n_rows: int | None = Field(default=None, ge=0)
    columns: list[str] = Field(default_factory=list)
    records: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    assets: list[DVCAnalysisAsset]
    provenance: dict[str, Any]


@dataclass(frozen=True)
class OperationContract:
    required_context: tuple[str, ...]
    approval_required: bool
    allowed_modes: tuple[str, ...] = ("exploratory", "confirmatory", "monitoring")


OPERATION_CONTRACTS: dict[str, OperationContract] = {
    "check_data_sanity": OperationContract(required_context=(), approval_required=False),
    "summarize_time_bins": OperationContract(
        required_context=("design.experimental_unit",), approval_required=True
    ),
    "summarize_light_dark": OperationContract(
        required_context=(
            "design.experimental_unit",
            "environment.timezone",
            "environment.light_schedule",
        ),
        approval_required=True,
    ),
    "summarize_circadian_cosinor": OperationContract(
        required_context=(
            "design.experimental_unit",
            "environment.timezone",
            "acquisition.temporal_resolution",
        ),
        approval_required=True,
    ),
}


class DVCAnalysisBlockedError(RuntimeError):
    """Raised when a scientific prerequisite or approval gate is not satisfied."""

    def __init__(self, message: str, *, blockers: list[str] | None = None):
        super().__init__(message)
        self.blockers = blockers or []


class DVCAnalysisError(RuntimeError):
    """Stable execution failure after governance gates have passed."""


def canonical_context_sha256(context: PreclinicalStudyContext) -> str:
    payload = json.dumps(context.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def canonical_parameters_sha256(parameters: dict[str, Any]) -> str:
    """Hash JSON analysis parameters with stable key ordering and no NaN values."""
    try:
        payload = json.dumps(
            parameters,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Analysis parameters must be canonical JSON values.") from exc
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def canonical_checkpoint_sha256(checkpoint: dict[str, Any]) -> str:
    """Hash a checkpoint payload independently of JSON whitespace."""
    try:
        payload = json.dumps(
            checkpoint,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Assessment checkpoint must be canonical JSON.") from exc
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _known(context: PreclinicalStudyContext, path: str) -> bool:
    current: Any = context
    for part in path.split("."):
        current = getattr(current, part)
    return current.status != EvidenceStatus.UNKNOWN and current.value is not None


def evaluate_prerequisites(request: DVCAnalysisRequest) -> list[str]:
    contract = OPERATION_CONTRACTS.get(request.operation)
    if contract is None:
        return [f"Operation '{request.operation}' is not approved for OpenScientist DVC execution."]

    blockers = [
        f"Missing required context: {path}"
        for path in contract.required_context
        if not _known(request.context, path)
    ]
    if request.context.design.mode not in contract.allowed_modes:
        blockers.append(
            f"Study mode '{request.context.design.mode}' is not allowed for {request.operation}."
        )

    if contract.approval_required:
        if request.approval is None:
            blockers.append(f"Human approval is required for {request.operation}.")
        else:
            expected_hash = canonical_context_sha256(request.context)
            expected_parameters_hash = canonical_parameters_sha256(request.parameters)
            if request.approval.dataset_id != request.dataset_id:
                blockers.append("Approval dataset does not match the requested dataset.")
            if request.approval.pre_analysis_checkpoint_id != request.pre_analysis_checkpoint_id:
                blockers.append(
                    "Approval checkpoint does not match the requested pre-analysis checkpoint."
                )
            if request.approval.context_sha256 != expected_hash:
                blockers.append(
                    "Approval is stale: its context hash does not match the current study context."
                )
            if request.approval.parameters_sha256 != expected_parameters_hash:
                blockers.append(
                    "Approval parameters do not match the canonical requested parameters."
                )
            if request.approval.approved_at > datetime.now(timezone.utc):
                blockers.append("Approval timestamp cannot be in the future.")
    return blockers


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _asset(job_dir: Path, path: Path, role: AnalysisAssetRole) -> DVCAnalysisAsset:
    return DVCAnalysisAsset(
        role=role,
        relative_path=str(path.relative_to(job_dir)),
        sha256=_sha256(path),
        bytes=path.stat().st_size,
    )


class DVCAnalysisService:
    def __init__(self, job_dir: Path) -> None:
        self.job_dir = Path(job_dir)

    def execute(self, request: DVCAnalysisRequest) -> DVCAnalysisResult:
        self._validate_pre_analysis_checkpoint(request)
        blockers = evaluate_prerequisites(request)
        if blockers:
            raise DVCAnalysisBlockedError("DVC analysis is blocked.", blockers=blockers)

        compatibility = inspect_udwa_compatibility()
        if not compatibility.compatible:
            details: list[str] = []
            if compatibility.missing_imports:
                details.append("missing imports: " + ", ".join(compatibility.missing_imports))
            if compatibility.missing_operations:
                details.append("missing operations: " + ", ".join(compatibility.missing_operations))
            raise DVCAnalysisError("Pinned UDWA dependency is incompatible: " + "; ".join(details))

        dataset_dir = self._dataset_dir(request.dataset_id)
        measurements_path = dataset_dir / "measurements.csv"
        manifest_path = dataset_dir / "manifest.json"
        if not measurements_path.is_file() or not manifest_path.is_file():
            raise DVCAnalysisError(
                "Dataset is incomplete: measurements.csv or manifest.json is missing."
            )

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self._verify_dataset_integrity(measurements_path, manifest)
        dataframe = pd.read_csv(measurements_path)

        started_at = datetime.now(timezone.utc)
        try:
            from udwa.orchestrator import run_tool  # type: ignore[import-untyped]

            output = run_tool(request.operation, dataframe, **request.parameters)
        except Exception as exc:  # noqa: BLE001
            raise DVCAnalysisError(
                f"UDWA execution failed: {redact_sensitive_text(str(exc))}"
            ) from exc
        if not isinstance(output, dict):
            raise DVCAnalysisError("UDWA execution returned an invalid result.")
        output = dict(redact_sensitive_data(output))
        if output.get("error"):
            raise DVCAnalysisError(f"UDWA execution failed: {output['error']}")
        completed_at = datetime.now(timezone.utc)

        execution_id = f"dvc-exec-{uuid4()}"
        execution_dir = self.job_dir / "dvc_analyses" / execution_id
        execution_dir.mkdir(parents=True, exist_ok=False)
        result_path = execution_dir / "result.json"
        provenance_path = execution_dir / "provenance.json"

        result_payload = {
            "schema": "openscientist-dvc-analysis-result/0.1",
            "execution_id": execution_id,
            "dataset_id": request.dataset_id,
            "operation": request.operation,
            "status": "completed",
            "output": output,
        }
        result_path.write_text(
            json.dumps(result_payload, indent=2, sort_keys=True, default=str), encoding="utf-8"
        )

        provenance = {
            "schema": "openscientist-dvc-analysis-provenance/0.1",
            "execution_id": execution_id,
            "dataset_id": request.dataset_id,
            "dataset_manifest_sha256": _sha256(manifest_path),
            "measurements_sha256": _sha256(measurements_path),
            "operation": request.operation,
            "parameters": request.parameters,
            "context_sha256": canonical_context_sha256(request.context),
            "pre_analysis_checkpoint_id": request.pre_analysis_checkpoint_id,
            "approval": request.approval.model_dump(mode="json") if request.approval else None,
            "udwa_distribution_version": compatibility.distribution_version,
            "udwa_pinned_commit": compatibility.pinned_commit,
            "openscientist_commit": os.environ.get("OPENSCIENTIST_COMMIT", "unknown"),
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "warnings": [str(item) for item in output.get("warnings", [])],
        }
        provenance_path.write_text(
            json.dumps(provenance, indent=2, sort_keys=True), encoding="utf-8"
        )

        return DVCAnalysisResult(
            execution_id=execution_id,
            dataset_id=request.dataset_id,
            operation=request.operation,
            specialist=output.get("specialist"),
            n_rows=output.get("n_rows"),
            columns=[str(item) for item in output.get("columns", [])],
            records=[dict(item) for item in output.get("records", [])],
            warnings=[str(item) for item in output.get("warnings", [])],
            assets=[
                _asset(self.job_dir, result_path, "result"),
                _asset(self.job_dir, provenance_path, "provenance"),
            ],
            provenance=provenance,
        )

    def _validate_pre_analysis_checkpoint(self, request: DVCAnalysisRequest) -> None:
        checkpoint_id = request.pre_analysis_checkpoint_id
        root = (self.job_dir / "dvc_assessments").resolve()
        path = (root / f"{checkpoint_id}.json").resolve()
        blocker = "A matching pre-analysis assessment checkpoint is required."
        if root not in path.parents or not path.is_file():
            raise DVCAnalysisBlockedError("DVC analysis is blocked.", blockers=[blocker])
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DVCAnalysisBlockedError(
                "DVC analysis is blocked.", blockers=["Pre-analysis checkpoint is invalid."]
            ) from exc
        if not isinstance(payload, dict):
            raise DVCAnalysisBlockedError(
                "DVC analysis is blocked.", blockers=["Pre-analysis checkpoint is invalid."]
            )
        expected_context_hash = canonical_context_sha256(request.context)
        mismatches = []
        if payload.get("checkpoint_id") != checkpoint_id:
            mismatches.append("checkpoint identity")
        if payload.get("checkpoint") != "pre_analysis":
            mismatches.append("checkpoint phase")
        if payload.get("dataset_id") != request.dataset_id:
            mismatches.append("dataset")
        if payload.get("context_sha256") != expected_context_hash:
            mismatches.append("study context")
        if request.approval and (
            request.approval.pre_analysis_checkpoint_sha256 != canonical_checkpoint_sha256(payload)
        ):
            mismatches.append("approved checkpoint content")
        if mismatches:
            raise DVCAnalysisBlockedError(
                "DVC analysis is blocked.",
                blockers=[
                    "Pre-analysis checkpoint does not match the requested "
                    + ", ".join(mismatches)
                    + "."
                ],
            )

    def _dataset_dir(self, dataset_id: str) -> Path:
        if not re.fullmatch(r"dvc-[0-9a-fA-F-]{36}", dataset_id):
            raise DVCAnalysisError("Invalid DVC dataset id.")
        dataset_dir = (self.job_dir / "dvc_datasets" / dataset_id).resolve()
        root = (self.job_dir / "dvc_datasets").resolve()
        if root not in dataset_dir.parents:
            raise DVCAnalysisError("Dataset path escapes the job directory.")
        if not dataset_dir.is_dir():
            raise DVCAnalysisError(f"DVC dataset not found: {dataset_id}")
        return dataset_dir

    @staticmethod
    def _verify_dataset_integrity(measurements_path: Path, manifest: dict[str, Any]) -> None:
        assets = manifest.get("result", {}).get("assets", [])
        expected = next(
            (
                item.get("sha256")
                for item in assets
                if item.get("role") == "normalized_measurements"
            ),
            None,
        )
        if not expected:
            raise DVCAnalysisError("Dataset manifest does not identify normalized measurements.")
        if _sha256(measurements_path) != expected:
            raise DVCAnalysisError("Dataset integrity check failed for normalized measurements.")
