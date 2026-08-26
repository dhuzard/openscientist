"""Governed deterministic UDWA execution for imported DVC datasets."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openscientist.assays import (
    AnalysisRunStore,
    ApprovalDecision,
    EvidenceArtifact,
    make_analysis_run_id,
)
from openscientist.assays import (
    OperationContract as GenericOperationContract,
)
from openscientist.integrations.dvc.adapter import DVC_OPERATION_CONTRACTS
from openscientist.integrations.dvc.security import (
    contains_sensitive_key,
    redact_sensitive_data,
    redact_sensitive_text,
)
from openscientist.integrations.dvc.workflow import DVCWorkflowStore
from openscientist.integrations.udwa import inspect_udwa_compatibility
from openscientist.preclinical_context.models import EvidenceStatus, PreclinicalStudyContext

AnalysisAssetRole = Literal["result", "provenance"]
OperationContract = GenericOperationContract


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


OPERATION_CONTRACTS = DVC_OPERATION_CONTRACTS


_BIOLOGICAL_TIME_OPERATIONS = frozenset({"summarize_light_dark", "summarize_circadian_cosinor"})
_VERIFIED_EVIDENCE_STATUSES = frozenset({EvidenceStatus.RECORDED, EvidenceStatus.COMPUTED})


class DVCAnalysisBlockedError(RuntimeError):
    """Raised when a scientific prerequisite or approval gate is not satisfied."""

    def __init__(self, message: str, *, blockers: list[str] | None = None):
        super().__init__(message)
        self.blockers = blockers or []


class DVCAnalysisError(RuntimeError):
    """Stable execution failure after governance gates have passed."""


def _validated_result_fields(output: dict[str, Any]) -> dict[str, Any]:
    specialist = output.get("specialist")
    n_rows = output.get("n_rows")
    columns = output.get("columns", [])
    records = output.get("records", [])
    warnings = output.get("warnings", [])
    if specialist is not None and not isinstance(specialist, str):
        raise DVCAnalysisError("UDWA execution returned an invalid specialist field.")
    if n_rows is not None and (
        not isinstance(n_rows, int) or isinstance(n_rows, bool) or n_rows < 0
    ):
        raise DVCAnalysisError("UDWA execution returned an invalid row count.")
    if not isinstance(columns, (list, tuple)):
        raise DVCAnalysisError("UDWA execution returned invalid result columns.")
    if not isinstance(records, (list, tuple)) or any(
        not isinstance(item, dict) for item in records
    ):
        raise DVCAnalysisError("UDWA execution returned invalid result records.")
    if not isinstance(warnings, (list, tuple)):
        raise DVCAnalysisError("UDWA execution returned invalid warnings.")
    return {
        "specialist": specialist,
        "n_rows": n_rows,
        "columns": [str(item) for item in columns],
        "records": [dict(item) for item in records],
        "warnings": [str(item) for item in warnings],
    }


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


def dvc_analysis_run_id(request: DVCAnalysisRequest) -> str:
    """Return the stable run identity shared by approval and execution."""

    return make_analysis_run_id(
        study_id=request.context.study_id,
        assay_id="dvc",
        dataset_id=request.dataset_id,
        operation_id=request.operation,
        context_sha256=canonical_context_sha256(request.context),
        parameters_sha256=canonical_parameters_sha256(request.parameters),
    )


def operation_contract_sha256(operation: str) -> str:
    """Return the immutable identity of one versioned scientific contract."""

    contract = OPERATION_CONTRACTS.get(operation)
    if contract is None:
        raise ValueError(f"Operation '{operation}' has no governed scientific contract.")
    payload = json.dumps(asdict(contract), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _analysis_request_sha256(
    request: DVCAnalysisRequest,
    *,
    checkpoint_sha256: str,
    dataset_manifest_sha256: str,
    measurements_sha256: str,
) -> str:
    payload = {
        "approval_id": request.approval.approval_id if request.approval else None,
        "checkpoint_sha256": checkpoint_sha256,
        "context_sha256": canonical_context_sha256(request.context),
        "dataset_id": request.dataset_id,
        "dataset_manifest_sha256": dataset_manifest_sha256,
        "measurements_sha256": measurements_sha256,
        "operation": request.operation,
        "operation_contract_sha256": operation_contract_sha256(request.operation),
        "parameters": request.parameters,
    }
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _known(context: PreclinicalStudyContext, path: str) -> bool:
    current: Any = context
    for part in path.split("."):
        current = getattr(current, part)
    return current.status != EvidenceStatus.UNKNOWN and current.value is not None


def _verified(context: PreclinicalStudyContext, path: str) -> bool:
    """Return whether a context value is source-backed rather than assumed."""
    current: Any = context
    for part in path.split("."):
        current = getattr(current, part)
    return (
        current.status in _VERIFIED_EVIDENCE_STATUSES
        and current.value is not None
        and isinstance(current.source, str)
        and bool(current.source.strip())
    )


def evaluate_prerequisites(request: DVCAnalysisRequest) -> list[str]:
    contract = OPERATION_CONTRACTS.get(request.operation)
    if contract is None:
        return [f"Operation '{request.operation}' is not approved for OpenScientist DVC execution."]

    blockers = [
        f"Missing required context: {path}"
        for path in contract.required_context
        if not _known(request.context, path)
    ]
    if request.operation in _BIOLOGICAL_TIME_OPERATIONS:
        for path in ("environment.timezone", "environment.light_schedule"):
            if _known(request.context, path) and not _verified(request.context, path):
                blockers.append(
                    f"Verified recorded or computed context with a source is required: {path}"
                )
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


def _assessment_conflict_blockers(
    checkpoint: dict[str, Any],
    operation: str,
) -> list[str]:
    """Block biological-time work when FAIR/PREPARE reports a relevant conflict."""
    if operation not in _BIOLOGICAL_TIME_OPERATIONS:
        return []
    relevant_terms = (
        "light",
        "dark",
        "timezone",
        "circadian",
        "phase",
        "zeitgeber",
        operation.lower(),
    )
    blockers: list[str] = []
    for assessment in checkpoint.get("assessments", []):
        if not isinstance(assessment, dict):
            continue
        for finding in assessment.get("findings", []):
            if not isinstance(finding, dict) or finding.get("status") != "conflicting":
                continue
            searchable = json.dumps(
                {
                    "requirement_id": finding.get("requirement_id"),
                    "missing_fields": finding.get("missing_fields"),
                    "blocks": finding.get("blocks"),
                    "recommendation": finding.get("recommendation"),
                },
                sort_keys=True,
                default=str,
            ).lower()
            if any(term in searchable for term in relevant_terms):
                requirement = str(finding.get("requirement_id") or "light-cycle metadata")
                blockers.append(
                    f"Conflicting assessment finding blocks biological-time analysis: {requirement}"
                )
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
        checkpoint = self._validate_pre_analysis_checkpoint(request)
        blockers = [
            *evaluate_prerequisites(request),
            *_assessment_conflict_blockers(checkpoint, request.operation),
        ]
        if blockers:
            raise DVCAnalysisBlockedError("DVC analysis is blocked.", blockers=blockers)

        dataset_dir = self._dataset_dir(request.dataset_id)
        manifest_path = dataset_dir / "manifest.json"
        if not manifest_path.is_file():
            raise DVCAnalysisError("Dataset is incomplete: manifest.json is missing.")

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        measurement_relpath = manifest.get("measurement_relpath", "measurements.csv")
        if not isinstance(measurement_relpath, str):
            raise DVCAnalysisError("Dataset manifest has an invalid measurement path.")
        measurements_path = (dataset_dir / measurement_relpath).resolve()
        if dataset_dir not in measurements_path.parents or not measurements_path.is_file():
            raise DVCAnalysisError("Dataset manifest measurement asset is missing or unsafe.")
        self._verify_dataset_integrity(measurements_path, manifest)
        checkpoint_sha256 = canonical_checkpoint_sha256(checkpoint)
        dataset_manifest_sha256 = _sha256(manifest_path)
        measurements_sha256 = _sha256(measurements_path)
        request_sha256 = _analysis_request_sha256(
            request,
            checkpoint_sha256=checkpoint_sha256,
            dataset_manifest_sha256=dataset_manifest_sha256,
            measurements_sha256=measurements_sha256,
        )

        existing = self._find_matching_execution(request, request_sha256=request_sha256)
        if existing is not None:
            self._resume_workflow(
                request,
                existing.execution_id,
                evidence_paths=tuple(
                    self.job_dir / asset.relative_path for asset in existing.assets
                ),
            )
            return existing

        self._resume_workflow(request)
        compatibility = inspect_udwa_compatibility()
        if not compatibility.compatible:
            details: list[str] = []
            if compatibility.missing_imports:
                details.append("missing imports: " + ", ".join(compatibility.missing_imports))
            if compatibility.missing_operations:
                details.append("missing operations: " + ", ".join(compatibility.missing_operations))
            error = DVCAnalysisError(
                "Pinned UDWA dependency is incompatible: " + "; ".join(details)
            )
            self._record_execution_failure(request, request_sha256, error)
            raise error

        dataframe = (
            pd.read_parquet(measurements_path)
            if measurements_path.suffix.casefold() == ".parquet"
            else pd.read_csv(measurements_path)
        )

        started_at = datetime.now(timezone.utc)
        try:
            run_tool = import_module("udwa.orchestrator").run_tool

            output = run_tool(request.operation, dataframe, **request.parameters)
        except Exception as exc:  # noqa: BLE001
            error = DVCAnalysisError(f"UDWA execution failed: {redact_sensitive_text(str(exc))}")
            self._record_execution_failure(request, request_sha256, error)
            raise error from exc
        if not isinstance(output, dict):
            error = DVCAnalysisError("UDWA execution returned an invalid result.")
            self._record_execution_failure(request, request_sha256, error)
            raise error
        output = dict(redact_sensitive_data(output))
        if output.get("error"):
            error = DVCAnalysisError(f"UDWA execution failed: {output['error']}")
            self._record_execution_failure(request, request_sha256, error)
            raise error
        try:
            result_fields = _validated_result_fields(output)
        except DVCAnalysisError as error:
            self._record_execution_failure(request, request_sha256, error)
            raise
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
            "analysis_run_id": dvc_analysis_run_id(request),
            "request_sha256": request_sha256,
            "dataset_id": request.dataset_id,
            "dataset_manifest_sha256": dataset_manifest_sha256,
            "measurements_sha256": measurements_sha256,
            "operation": request.operation,
            "operation_contract": asdict(OPERATION_CONTRACTS[request.operation]),
            "operation_contract_sha256": operation_contract_sha256(request.operation),
            "parameters": request.parameters,
            "context_sha256": canonical_context_sha256(request.context),
            "pre_analysis_checkpoint_id": request.pre_analysis_checkpoint_id,
            "pre_analysis_checkpoint_sha256": checkpoint_sha256,
            "approval": request.approval.model_dump(mode="json") if request.approval else None,
            "udwa_distribution_version": compatibility.distribution_version,
            "udwa_pinned_commit": compatibility.pinned_commit,
            "openscientist_commit": os.environ.get("OPENSCIENTIST_COMMIT", "unknown"),
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "warnings": [str(item) for item in output.get("warnings", [])],
            "scientific_prerequisites": {
                path: {
                    "status": getattr(request.context, section).__getattribute__(field).status,
                    "source": getattr(request.context, section).__getattribute__(field).source,
                    "value_sha256": hashlib.sha256(
                        json.dumps(
                            getattr(request.context, section).__getattribute__(field).value,
                            sort_keys=True,
                            default=str,
                        ).encode("utf-8")
                    ).hexdigest(),
                }
                for path in OPERATION_CONTRACTS[request.operation].required_context
                for section, field in [path.split(".", maxsplit=1)]
            },
        }
        provenance_path.write_text(
            json.dumps(provenance, indent=2, sort_keys=True), encoding="utf-8"
        )

        self._resume_workflow(
            request,
            execution_id,
            evidence_paths=(result_path, provenance_path),
        )

        return DVCAnalysisResult(
            execution_id=execution_id,
            dataset_id=request.dataset_id,
            operation=request.operation,
            assets=[
                _asset(self.job_dir, result_path, "result"),
                _asset(self.job_dir, provenance_path, "provenance"),
            ],
            provenance=provenance,
            **result_fields,
        )

    def _find_matching_execution(
        self,
        request: DVCAnalysisRequest,
        *,
        request_sha256: str,
    ) -> DVCAnalysisResult | None:
        analysis_root = self.job_dir / "dvc_analyses"
        if not analysis_root.is_dir():
            return None
        matches: list[DVCAnalysisResult] = []
        for provenance_path in sorted(analysis_root.glob("*/provenance.json")):
            result_path = provenance_path.with_name("result.json")
            try:
                prov = json.loads(provenance_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise DVCAnalysisError(
                    "Existing DVC analysis provenance is unreadable; refusing a potentially "
                    "duplicate deterministic execution."
                ) from exc
            if not isinstance(prov, dict):
                raise DVCAnalysisError(
                    "Existing DVC analysis provenance is invalid; refusing a potentially "
                    "duplicate deterministic execution."
                )
            if prov.get("request_sha256") != request_sha256:
                if self._is_legacy_request_match(prov, request):
                    raise DVCAnalysisError(
                        "A completed matching execution predates versioned idempotency. "
                        "Refusing a silent deterministic rerun; review or supersede it explicitly."
                    )
                continue
            if not result_path.is_file():
                raise DVCAnalysisError(
                    "A matching completed execution has no result artifact; refusing rerun."
                )
            try:
                res = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise DVCAnalysisError(
                    "A matching completed execution has an invalid result artifact; refusing rerun."
                ) from exc
            if not isinstance(res, dict) or not isinstance(res.get("output"), dict):
                raise DVCAnalysisError(
                    "A matching completed execution has an invalid result payload; refusing rerun."
                )
            execution_id = provenance_path.parent.name
            if (
                res.get("status") != "completed"
                or res.get("execution_id") != execution_id
                or prov.get("execution_id") != execution_id
                or res.get("dataset_id") != request.dataset_id
                or res.get("operation") != request.operation
            ):
                raise DVCAnalysisError(
                    "A matching completed execution has conflicting provenance; refusing rerun."
                )
            output = res["output"]
            try:
                result_fields = _validated_result_fields(output)
            except DVCAnalysisError as exc:
                raise DVCAnalysisError(
                    "A matching completed execution has invalid output evidence; refusing rerun."
                ) from exc
            matches.append(
                DVCAnalysisResult(
                    execution_id=execution_id,
                    dataset_id=request.dataset_id,
                    operation=request.operation,
                    assets=[
                        _asset(self.job_dir, result_path, "result"),
                        _asset(self.job_dir, provenance_path, "provenance"),
                    ],
                    provenance=prov,
                    **result_fields,
                )
            )
        if len(matches) > 1:
            raise DVCAnalysisError(
                "Multiple completed executions have the same deterministic request identity."
            )
        return matches[0] if matches else None

    @staticmethod
    def _is_legacy_request_match(provenance: dict[str, Any], request: DVCAnalysisRequest) -> bool:
        return (
            "request_sha256" not in provenance
            and provenance.get("dataset_id") == request.dataset_id
            and provenance.get("operation") == request.operation
            and provenance.get("parameters") == request.parameters
            and provenance.get("context_sha256") == canonical_context_sha256(request.context)
            and provenance.get("pre_analysis_checkpoint_id") == request.pre_analysis_checkpoint_id
        )

    def _resume_workflow(
        self,
        request: DVCAnalysisRequest,
        execution_id: str | None = None,
        evidence_paths: tuple[Path, ...] = (),
    ) -> None:
        store = self._workflow_store(request)
        store.record_dataset(request.dataset_id)
        store.record_checkpoint(
            request.pre_analysis_checkpoint_id,
            is_pre=True,
            context_sha256=canonical_context_sha256(request.context),
        )
        if request.approval is not None:
            decision = ApprovalDecision(
                approval_id=request.approval.approval_id,
                run_id=store.run_id,
                assay_id="dvc",
                dataset_id=request.dataset_id,
                operation_id=request.operation,
                contract_sha256=operation_contract_sha256(request.operation),
                context_sha256=request.approval.context_sha256,
                parameters_sha256=request.approval.parameters_sha256,
                decided_by=request.approval.approved_by,
                decided_at=request.approval.approved_at,
                decision="approved",
            )
            store.record_approval(
                request.approval.approval_id,
                checkpoint_id=request.pre_analysis_checkpoint_id,
                dataset_id=request.dataset_id,
                actor=request.approval.approved_by,
                decision=decision,
            )
        if execution_id is not None:
            store.record_execution(
                execution_id,
                dataset_id=request.dataset_id,
                operation=request.operation,
            )
            for path in evidence_paths:
                role = "result" if path.name == "result.json" else "provenance"
                store.record_evidence(
                    EvidenceArtifact(
                        artifact_id=f"{execution_id}:{role}",
                        run_id=store.run_id,
                        assay_id="dvc",
                        dataset_id=request.dataset_id,
                        role=role,
                        relative_path=str(path.relative_to(self.job_dir)),
                        sha256=_sha256(path),
                        bytes=path.stat().st_size,
                        media_type="application/json",
                    )
                )
        # Maintain the documented legacy job-level summary as a compatibility
        # projection. The run-scoped record above is the canonical workflow.
        legacy = DVCWorkflowStore(self.job_dir)
        legacy.record_dataset(request.dataset_id)
        legacy.record_checkpoint(
            request.pre_analysis_checkpoint_id,
            is_pre=True,
            context_sha256=canonical_context_sha256(request.context),
        )
        if request.approval is not None:
            legacy.record_approval(
                request.approval.approval_id,
                checkpoint_id=request.pre_analysis_checkpoint_id,
                dataset_id=request.dataset_id,
                actor=request.approval.approved_by,
            )
        if execution_id is not None:
            legacy.record_execution(
                execution_id,
                dataset_id=request.dataset_id,
                operation=request.operation,
            )

    def _workflow_store(self, request: DVCAnalysisRequest) -> AnalysisRunStore:
        return DVCWorkflowStore.for_dvc_analysis(
            self.job_dir,
            study_id=request.context.study_id,
            dataset_id=request.dataset_id,
            operation_id=request.operation,
            context_sha256=canonical_context_sha256(request.context),
            parameters_sha256=canonical_parameters_sha256(request.parameters),
        )

    def _record_execution_failure(
        self,
        request: DVCAnalysisRequest,
        request_sha256: str,
        error: Exception,
    ) -> None:
        message_hash = hashlib.sha256(str(error).encode("utf-8")).hexdigest()
        store = self._workflow_store(request)
        # A failed execution is still attached to a fully identified run.
        store.record_dataset(request.dataset_id)
        store.record_checkpoint(
            request.pre_analysis_checkpoint_id,
            is_pre=True,
            context_sha256=canonical_context_sha256(request.context),
        )
        if request.approval is not None:
            store.record_approval(
                request.approval.approval_id,
                checkpoint_id=request.pre_analysis_checkpoint_id,
                dataset_id=request.dataset_id,
                actor=request.approval.approved_by,
            )
        store.record_failure(
            "governed_udwa_execution",
            error,
            idempotency_key=f"execution-failure:{request_sha256}:{message_hash}",
            actor="analysis_service",
        )

    def _validate_pre_analysis_checkpoint(self, request: DVCAnalysisRequest) -> dict[str, Any]:
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
        return payload

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
        prepared_hashes = manifest.get("artifact_sha256")
        if isinstance(prepared_hashes, dict):
            expected = prepared_hashes.get(measurements_path.name)
            if not isinstance(expected, str):
                raise DVCAnalysisError(
                    "Prepared dataset manifest does not identify normalized measurements."
                )
            if _sha256(measurements_path) != expected:
                raise DVCAnalysisError(
                    "Dataset integrity check failed for normalized measurements."
                )
            return
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
