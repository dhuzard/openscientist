"""Evidence-derived governance status for DVC job outputs.

This module deliberately does not inspect the scientific report. Report prose is
agent-authored and must never be treated as proof of assessment, approval, or
governed execution. The status below is reconstructed from immutable-ish DVC
artifacts and structured tool transcript events, and fails closed whenever an
execution chain cannot be verified.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from openscientist.integrations.dvc.execution import (
    OPERATION_CONTRACTS,
    DVCAnalysisApproval,
    canonical_checkpoint_sha256,
    canonical_parameters_sha256,
)

EvidenceKind = Literal[
    "validation_diagnostic",
    "exploratory_computation",
    "governance_block",
    "approved_governed_analysis",
    "unverified_analysis",
]

_DVC_TOOL_NAMES = {
    "dvc_assess_pre_analysis",
    "dvc_assess_post_analysis",
    "dvc_run_analysis",
}
_DIAGNOSTIC_TERMS = (
    " qc",
    "cadence",
    "column",
    "completeness",
    "coverage",
    "data binding",
    "data contract",
    "duplicate",
    "integrity",
    "missing",
    "nonnegative",
    "quality control",
    "reconstruction",
    "sanity",
    "schema",
    "shape",
    "validation",
)
_INFERENCE_TERMS = (
    "anova",
    "bootstrap",
    "correlation",
    "cosinor",
    "effect size",
    "fit(",
    "linear model",
    "mannwhitney",
    "mixedlm",
    "model",
    "pearson",
    "regression",
    "scipy.stats",
    "spearman",
    "statistical",
    "ttest",
    "wilcoxon",
)


@dataclass(frozen=True)
class DVCGovernanceEvidence:
    """One machine-readable observation supporting a displayed status."""

    kind: EvidenceKind
    source: str
    operation: str
    identifier: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class DVCGovernanceStatus:
    """Conservative, non-exclusive status facets for one DVC job."""

    datasets: tuple[str, ...]
    validation_diagnostics: tuple[DVCGovernanceEvidence, ...]
    exploratory_computations: tuple[DVCGovernanceEvidence, ...]
    governance_blocks: tuple[DVCGovernanceEvidence, ...]
    approved_governed_analyses: tuple[DVCGovernanceEvidence, ...]
    unverified_analyses: tuple[DVCGovernanceEvidence, ...]

    @property
    def primary_state(self) -> str:
        if self.approved_governed_analyses and (
            self.exploratory_computations or self.unverified_analyses
        ):
            return "mixed"
        if (self.governance_blocks or self.unverified_analyses) and not (
            self.approved_governed_analyses
        ):
            return "blocked"
        if self.exploratory_computations:
            return "exploratory"
        if self.approved_governed_analyses:
            return "approved"
        return "diagnostic"

    @property
    def summary(self) -> str:
        if self.primary_state == "mixed":
            return (
                "Approved governed results and separate unapproved exploratory "
                "computations are both present. Interpret each result only within its badge."
            )
        if self.primary_state == "blocked":
            return (
                "The governed DVC chain is blocked. Any available diagnostics or exploratory "
                "outputs are not an approved governed analysis."
            )
        if self.primary_state == "exploratory":
            return (
                "DVC-linked computation occurred outside a verified approval chain. "
                "Treat those outputs as exploratory, not governed scientific results."
            )
        if self.primary_state == "approved":
            return (
                "At least one approval-required DVC execution has a complete, "
                "cryptographically consistent checkpoint and approval chain."
            )
        return (
            "Only DVC import or validation evidence is available. "
            "No approved governed scientific analysis was verified."
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a stable representation suitable for APIs or persisted snapshots."""
        return {
            "schema": "openscientist-dvc-governance-status/0.1",
            "primary_state": self.primary_state,
            "summary": self.summary,
            **asdict(self),
        }


def derive_dvc_governance_status(job_dir: Path) -> DVCGovernanceStatus | None:
    """Derive DVC governance facets from artifacts and structured transcript events.

    ``None`` means that the job contains no machine-readable DVC evidence and the
    DVC-specific banner should not be rendered.
    """

    job_dir = Path(job_dir)
    datasets, invalid_datasets = _scan_datasets(job_dir)
    diagnostics: list[DVCGovernanceEvidence] = []
    exploratory: list[DVCGovernanceEvidence] = []
    blocks: list[DVCGovernanceEvidence] = []
    approved: list[DVCGovernanceEvidence] = []
    unverified: list[DVCGovernanceEvidence] = list(invalid_datasets)

    transcript_evidence = _read_transcript_evidence(job_dir, datasets)
    diagnostics.extend(transcript_evidence["diagnostics"])
    exploratory.extend(transcript_evidence["exploratory"])
    blocks.extend(transcript_evidence["blocks"])

    for execution_dir in _analysis_directories(job_dir):
        evidence, is_diagnostic = _verify_analysis_execution(job_dir, execution_dir)
        if evidence.kind == "approved_governed_analysis":
            approved.append(evidence)
        elif evidence.kind == "unverified_analysis":
            unverified.append(evidence)
        elif is_diagnostic:
            diagnostics.append(evidence)

    has_dvc_evidence = bool(
        datasets or diagnostics or exploratory or blocks or approved or unverified
    )
    if not has_dvc_evidence:
        return None
    return DVCGovernanceStatus(
        datasets=datasets,
        validation_diagnostics=tuple(diagnostics),
        exploratory_computations=tuple(exploratory),
        governance_blocks=tuple(blocks),
        approved_governed_analyses=tuple(approved),
        unverified_analyses=tuple(unverified),
    )


def _scan_datasets(
    job_dir: Path,
) -> tuple[tuple[str, ...], tuple[DVCGovernanceEvidence, ...]]:
    root = job_dir / "dvc_datasets"
    if not root.is_dir():
        return (), ()
    dataset_ids = []
    invalid = []
    for dataset_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        manifest_path = dataset_dir / "manifest.json"
        payload = _read_json_object(manifest_path)
        dataset_id = dataset_dir.name
        reasons = _dataset_integrity_errors(job_dir, dataset_id, payload)
        if not reasons:
            dataset_ids.append(dataset_id)
        else:
            invalid.append(
                DVCGovernanceEvidence(
                    kind="unverified_analysis",
                    source=str(manifest_path.relative_to(job_dir)).replace("\\", "/"),
                    operation="dvc_import_dataset",
                    identifier=dataset_id,
                    detail="; ".join(reasons),
                )
            )
    return tuple(dataset_ids), tuple(invalid)


def _dataset_integrity_errors(
    job_dir: Path,
    dataset_id: str,
    manifest: dict[str, Any] | None,
) -> list[str]:
    if not re.fullmatch(r"dvc-[0-9a-fA-F-]{36}", dataset_id):
        return ["dataset directory does not have a valid DVC dataset id"]
    if manifest is None:
        return ["dataset manifest is missing or invalid"]
    reasons = []
    if manifest.get("schema") != "openscientist-dvc-dataset/0.1":
        reasons.append("unexpected dataset manifest schema")
    result = manifest.get("result")
    if not isinstance(result, dict):
        return [*reasons, "dataset manifest result is missing"]
    if result.get("dataset_id") not in (None, dataset_id):
        reasons.append("manifest dataset id does not match its directory")
    assets = result.get("assets")
    measurement_asset = (
        next(
            (
                item
                for item in assets
                if isinstance(item, dict) and item.get("role") == "normalized_measurements"
            ),
            None,
        )
        if isinstance(assets, list)
        else None
    )
    if measurement_asset is None:
        return [*reasons, "manifest does not identify normalized measurements"]
    relative_path = measurement_asset.get("relative_path")
    asset_path = (
        (job_dir / relative_path).resolve()
        if isinstance(relative_path, str)
        else (job_dir / "dvc_datasets" / dataset_id / "measurements.csv").resolve()
    )
    job_root = job_dir.resolve()
    if job_root not in asset_path.parents or not asset_path.is_file():
        reasons.append("normalized measurements asset is missing or escapes the job")
    elif measurement_asset.get("sha256") != _sha256(asset_path):
        reasons.append("normalized measurements hash does not match the manifest")
    return reasons


def _analysis_directories(job_dir: Path) -> tuple[Path, ...]:
    root = job_dir / "dvc_analyses"
    if not root.is_dir():
        return ()
    return tuple(
        path
        for path in sorted(root.iterdir())
        if path.is_dir()
        and ((path / "result.json").is_file() or (path / "provenance.json").is_file())
    )


def _read_transcript_evidence(
    job_dir: Path,
    dataset_ids: tuple[str, ...],
) -> dict[str, list[DVCGovernanceEvidence]]:
    evidence: dict[str, list[DVCGovernanceEvidence]] = {
        "diagnostics": [],
        "exploratory": [],
        "blocks": [],
    }
    transcript_root = job_dir / "provenance"
    if not transcript_root.is_dir():
        return evidence

    for path in sorted(transcript_root.glob("*transcript.json")):
        payload = _read_json_value(path)
        if not isinstance(payload, list):
            continue
        calls = {
            item.get("id"): item
            for item in payload
            if isinstance(item, dict)
            and item.get("type") == "tool_call"
            and isinstance(item.get("id"), str)
        }
        for item in payload:
            if not isinstance(item, dict) or item.get("type") != "tool_result":
                continue
            call = calls.get(item.get("call_id"))
            if not isinstance(call, dict):
                continue
            tool = call.get("tool")
            if tool in _DVC_TOOL_NAMES:
                block = _governance_block_from_result(path, call, item)
                if block is not None:
                    evidence["blocks"].append(block)
            elif tool == "execute_code" and _tool_result_succeeded(item):
                computation = _code_computation_evidence(path, call, dataset_ids)
                if computation is not None:
                    bucket = (
                        "diagnostics"
                        if computation.kind == "validation_diagnostic"
                        else "exploratory"
                    )
                    evidence[bucket].append(computation)
    return evidence


def _governance_block_from_result(
    path: Path,
    call: dict[str, Any],
    result: dict[str, Any],
) -> DVCGovernanceEvidence | None:
    structured = result.get("structured_content")
    if not isinstance(structured, dict) or structured.get("ok") is not False:
        return None
    error_type = structured.get("error_type")
    error = structured.get("error")
    blockers = structured.get("blockers")
    details = [str(error_type)] if error_type else []
    if error:
        details.append(str(error))
    if isinstance(blockers, list):
        details.extend(str(item) for item in blockers)
    arguments = call.get("arguments")
    identifier = arguments.get("dataset_id") if isinstance(arguments, dict) else None
    return DVCGovernanceEvidence(
        kind="governance_block",
        source=_relative_provenance_path(path),
        operation=str(call.get("tool")),
        identifier=str(identifier) if identifier else None,
        detail=": ".join(details)[:800] or "Structured DVC tool failure",
    )


def _code_computation_evidence(
    path: Path,
    call: dict[str, Any],
    dataset_ids: tuple[str, ...],
) -> DVCGovernanceEvidence | None:
    arguments = call.get("arguments")
    if not isinstance(arguments, dict):
        return None
    description = str(arguments.get("description") or "")
    code = str(arguments.get("code") or "")
    combined = f"{description}\n{code}".lower()
    if not _is_dvc_linked(combined, dataset_ids):
        return None

    has_inference = any(term in combined for term in _INFERENCE_TERMS)
    has_diagnostic_scope = any(term in combined for term in _DIAGNOSTIC_TERMS)
    kind: EvidenceKind = (
        "validation_diagnostic"
        if has_diagnostic_scope and not has_inference
        else "exploratory_computation"
    )
    return DVCGovernanceEvidence(
        kind=kind,
        source=_relative_provenance_path(path),
        operation="execute_code",
        identifier=str(call.get("id") or "") or None,
        detail=description[:500] or "DVC-linked execute_code call",
    )


def _is_dvc_linked(text: str, dataset_ids: tuple[str, ...]) -> bool:
    if any(dataset_id.lower() in text for dataset_id in dataset_ids):
        return True
    return any(
        term in text
        for term in (
            "dvc",
            "digital ventilated cage",
            "activation",
            "circadian",
            "cage-level",
            "hourly cage",
        )
    )


def _tool_result_succeeded(result: dict[str, Any]) -> bool:
    if result.get("success") is not True or result.get("status") != "completed":
        return False
    structured = result.get("structured_content")
    return not isinstance(structured, dict) or structured.get("ok") is not False


def _verify_analysis_execution(
    job_dir: Path,
    execution_dir: Path,
) -> tuple[DVCGovernanceEvidence, bool]:
    result_path = execution_dir / "result.json"
    provenance_path = execution_dir / "provenance.json"
    result = _read_json_object(result_path)
    provenance = _read_json_object(provenance_path)
    identifier = execution_dir.name
    operation = "unknown"
    reasons: list[str] = []

    if result is None or provenance is None:
        reasons.append("result.json or provenance.json is missing or invalid")
    else:
        identifier = str(result.get("execution_id") or execution_dir.name)
        operation = str(result.get("operation") or provenance.get("operation") or "unknown")
        if result.get("schema") != "openscientist-dvc-analysis-result/0.1":
            reasons.append("unexpected result schema")
        if provenance.get("schema") != "openscientist-dvc-analysis-provenance/0.1":
            reasons.append("unexpected provenance schema")
        if result.get("status") != "completed":
            reasons.append("execution is not completed")
        for field in ("execution_id", "dataset_id", "operation"):
            if result.get(field) != provenance.get(field):
                reasons.append(f"{field} differs between result and provenance")
        reasons.extend(_verify_dataset_hashes(job_dir, provenance))

    contract = OPERATION_CONTRACTS.get(operation)
    is_diagnostic = bool(contract and not contract.approval_required)
    if result is not None and provenance is not None and not reasons and is_diagnostic:
        return (
            DVCGovernanceEvidence(
                kind="validation_diagnostic",
                source=str(result_path.relative_to(job_dir)).replace("\\", "/"),
                operation=operation,
                identifier=identifier,
                detail="Verified governed diagnostic execution (approval not required)",
            ),
            True,
        )

    if contract is None:
        reasons.append("operation is not allowlisted")
    elif contract.approval_required and result is not None and provenance is not None:
        reasons.extend(_verify_approval_chain(job_dir, provenance))
    elif not is_diagnostic:
        reasons.append("operation contract is not approval-required")

    if reasons:
        return (
            DVCGovernanceEvidence(
                kind="unverified_analysis",
                source=str(result_path.relative_to(job_dir)).replace("\\", "/"),
                operation=operation,
                identifier=identifier,
                detail="; ".join(dict.fromkeys(reasons))[:1000],
            ),
            False,
        )
    return (
        DVCGovernanceEvidence(
            kind="approved_governed_analysis",
            source=str(result_path.relative_to(job_dir)).replace("\\", "/"),
            operation=operation,
            identifier=identifier,
            detail="Verified checkpoint, authenticated approval, dataset hashes, and provenance",
        ),
        False,
    )


def _verify_dataset_hashes(job_dir: Path, provenance: dict[str, Any]) -> list[str]:
    dataset_id = provenance.get("dataset_id")
    if not isinstance(dataset_id, str) or not re.fullmatch(
        r"dvc-[0-9a-fA-F-]{36}",
        dataset_id,
    ):
        return ["dataset id is missing or invalid"]
    dataset_dir = job_dir / "dvc_datasets" / dataset_id
    manifest_path = dataset_dir / "manifest.json"
    measurements_path = dataset_dir / "measurements.csv"
    reasons = []
    if not manifest_path.is_file() or not measurements_path.is_file():
        return ["dataset manifest or normalized measurements are missing"]
    if provenance.get("dataset_manifest_sha256") != _sha256(manifest_path):
        reasons.append("dataset manifest hash does not match provenance")
    if provenance.get("measurements_sha256") != _sha256(measurements_path):
        reasons.append("normalized measurements hash does not match provenance")
    return reasons


def _verify_approval_chain(job_dir: Path, provenance: dict[str, Any]) -> list[str]:
    raw_approval = provenance.get("approval")
    try:
        approval = DVCAnalysisApproval.model_validate(raw_approval)
    except Exception:  # noqa: BLE001
        return ["embedded approval is missing or invalid"]

    reasons = []
    if approval.dataset_id != provenance.get("dataset_id"):
        reasons.append("approval dataset does not match provenance")
    if approval.operation != provenance.get("operation"):
        reasons.append("approval operation does not match provenance")
    if approval.context_sha256 != provenance.get("context_sha256"):
        reasons.append("approval context does not match provenance")
    try:
        parameters_hash = canonical_parameters_sha256(provenance.get("parameters", {}))
    except ValueError:
        reasons.append("provenance parameters are not canonical JSON")
    else:
        if approval.parameters_sha256 != parameters_hash:
            reasons.append("approval parameters do not match provenance")

    approval_path = job_dir / "dvc_approvals" / f"{approval.approval_id}.json"
    stored_approval = _read_json_object(approval_path)
    try:
        stored = DVCAnalysisApproval.model_validate(stored_approval)
    except Exception:  # noqa: BLE001
        reasons.append("trusted approval record is missing or invalid")
    else:
        if stored != approval:
            reasons.append("embedded approval differs from trusted approval record")

    audit_path = approval_path.with_name(f"{approval.approval_id}.audit.json")
    audit = _read_json_object(audit_path)
    if (
        audit is None
        or audit.get("schema") != "openscientist-dvc-approval-audit/0.1"
        or audit.get("created_via") != "authenticated_rest_api"
        or audit.get("approval_id") != approval.approval_id
        or audit.get("dataset_id") != approval.dataset_id
        or audit.get("pre_analysis_checkpoint_id") != approval.pre_analysis_checkpoint_id
    ):
        reasons.append("authenticated approval audit is missing or inconsistent")

    checkpoint_path = job_dir / "dvc_assessments" / f"{approval.pre_analysis_checkpoint_id}.json"
    checkpoint = _read_json_object(checkpoint_path)
    if checkpoint is None:
        reasons.append("pre-analysis checkpoint is missing or invalid")
    else:
        if checkpoint.get("checkpoint_id") != approval.pre_analysis_checkpoint_id:
            reasons.append("checkpoint identity does not match approval")
        if checkpoint.get("checkpoint") != "pre_analysis":
            reasons.append("approved checkpoint is not pre-analysis")
        if checkpoint.get("dataset_id") != approval.dataset_id:
            reasons.append("checkpoint dataset does not match approval")
        if checkpoint.get("context_sha256") != approval.context_sha256:
            reasons.append("checkpoint context does not match approval")
        try:
            checkpoint_hash = canonical_checkpoint_sha256(checkpoint)
        except ValueError:
            reasons.append("checkpoint is not canonical JSON")
        else:
            if checkpoint_hash != approval.pre_analysis_checkpoint_sha256:
                reasons.append("checkpoint content hash does not match approval")

    approved_at = approval.approved_at
    started_at = _parse_datetime(provenance.get("started_at"))
    if started_at is None:
        reasons.append("analysis start timestamp is missing or invalid")
    elif approved_at > started_at:
        reasons.append("approval was issued after analysis started")
    return reasons


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json_object(path: Path) -> dict[str, Any] | None:
    value = _read_json_value(path)
    return value if isinstance(value, dict) else None


def _read_json_value(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _relative_provenance_path(path: Path) -> str:
    return f"provenance/{path.name}"
