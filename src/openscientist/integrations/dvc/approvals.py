"""Read-only and governed approval records for DVC analyses.

Approval files are created by a trusted UI or workflow outside the agent MCP tool.
The agent may reference an approval id but cannot create or modify approvals here.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from openscientist.assays import ApprovalDecision
from openscientist.integrations.dvc.execution import (
    OPERATION_CONTRACTS,
    DVCAnalysisApproval,
    canonical_checkpoint_sha256,
    canonical_context_sha256,
    canonical_parameters_sha256,
    operation_contract_sha256,
)
from openscientist.integrations.dvc.workflow import DVCWorkflowStore
from openscientist.preclinical_context.models import PreclinicalStudyContext


class DVCApprovalNotFoundError(RuntimeError):
    """No trusted approval record exists for the supplied id."""


class FileDVCApprovalStore:
    def __init__(self, job_dir: Path) -> None:
        self.root = Path(job_dir) / "dvc_approvals"

    def resolve(self, approval_id: str) -> DVCAnalysisApproval:
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,200}", approval_id):
            raise DVCApprovalNotFoundError("Invalid DVC approval id.")
        path = (self.root / f"{approval_id}.json").resolve()
        root = self.root.resolve()
        if root not in path.parents or not path.is_file():
            raise DVCApprovalNotFoundError(f"DVC approval not found: {approval_id}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            approval = DVCAnalysisApproval.model_validate(payload)
        except Exception as exc:  # noqa: BLE001
            raise DVCApprovalNotFoundError(
                f"DVC approval record is invalid: {approval_id}"
            ) from exc
        if approval.approval_id != approval_id:
            raise DVCApprovalNotFoundError("DVC approval id does not match its record.")
        return approval

    def list_approvals(self) -> list[DVCAnalysisApproval]:
        if not self.root.is_dir():
            return []
        approvals: list[DVCAnalysisApproval] = []
        for path in sorted(self.root.glob("approval-*.json")):
            if path.name.endswith(".audit.json"):
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                approvals.append(DVCAnalysisApproval.model_validate(payload))
            except Exception:
                continue
        return approvals


def create_dvc_approval_record(
    job_dir: Path,
    dataset_id: str,
    pre_analysis_checkpoint_id: str,
    operation: str,
    context: PreclinicalStudyContext,
    parameters: dict[str, Any] | None = None,
    approved_by: str = "authenticated_user",
    created_via: str = "web_ui",
) -> DVCAnalysisApproval:
    """Create and persist an authenticated DVC approval record."""
    if operation not in OPERATION_CONTRACTS:
        raise ValueError(f"Operation '{operation}' is not governed for DVC execution.")
    if not re.fullmatch(r"dvc-[0-9a-fA-F-]{36}", dataset_id):
        raise ValueError("Invalid DVC dataset id.")
    if not re.fullmatch(r"dvc-assess-[0-9a-fA-F-]{36}", pre_analysis_checkpoint_id):
        raise ValueError("Invalid DVC pre-analysis checkpoint id.")

    context_sha256 = canonical_context_sha256(context)
    checkpoint_root = (Path(job_dir) / "dvc_assessments").resolve()
    checkpoint_path = (checkpoint_root / f"{pre_analysis_checkpoint_id}.json").resolve()
    if checkpoint_root not in checkpoint_path.parents or not checkpoint_path.is_file():
        raise FileNotFoundError(f"Pre-analysis checkpoint not found: {pre_analysis_checkpoint_id}")

    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    if checkpoint.get("checkpoint") != "pre_analysis":
        raise ValueError("Supplied checkpoint is not a pre-analysis assessment checkpoint.")
    if checkpoint.get("checkpoint_id") != pre_analysis_checkpoint_id:
        raise ValueError("Checkpoint identity does not match its filename.")
    if checkpoint.get("dataset_id") != dataset_id:
        raise ValueError("Checkpoint dataset_id does not match.")
    if checkpoint.get("context_sha256") != context_sha256:
        raise ValueError("Checkpoint context_sha256 does not match current context.")

    parameters_sha256 = canonical_parameters_sha256(parameters or {})
    workflow = DVCWorkflowStore.for_dvc_analysis(
        job_dir,
        study_id=context.study_id,
        dataset_id=dataset_id,
        operation_id=operation,
        context_sha256=context_sha256,
        parameters_sha256=parameters_sha256,
    )
    workflow.record_dataset(dataset_id)
    workflow.record_checkpoint(
        pre_analysis_checkpoint_id,
        is_pre=True,
        context_sha256=context_sha256,
    )
    legacy_workflow = DVCWorkflowStore(job_dir)
    legacy_workflow.record_dataset(dataset_id)
    legacy_workflow.record_checkpoint(
        pre_analysis_checkpoint_id,
        is_pre=True,
        context_sha256=context_sha256,
    )
    store = FileDVCApprovalStore(job_dir)
    for existing in store.list_approvals():
        if (
            existing.dataset_id == dataset_id
            and existing.pre_analysis_checkpoint_id == pre_analysis_checkpoint_id
            and existing.operation == operation
            and existing.context_sha256 == context_sha256
            and existing.parameters_sha256 == parameters_sha256
        ):
            workflow.record_approval(
                existing.approval_id,
                checkpoint_id=pre_analysis_checkpoint_id,
                dataset_id=dataset_id,
                actor=approved_by,
                decision=ApprovalDecision(
                    approval_id=existing.approval_id,
                    run_id=workflow.run_id,
                    assay_id="dvc",
                    dataset_id=dataset_id,
                    operation_id=operation,
                    contract_sha256=operation_contract_sha256(operation),
                    context_sha256=context_sha256,
                    parameters_sha256=parameters_sha256,
                    decided_by=existing.approved_by,
                    decided_at=existing.approved_at,
                    decision="approved",
                ),
            )
            legacy_workflow.record_approval(
                existing.approval_id,
                checkpoint_id=pre_analysis_checkpoint_id,
                dataset_id=dataset_id,
                actor=approved_by,
            )
            return existing

    approval_id = f"approval-{uuid4()}"
    approval = DVCAnalysisApproval(
        approval_id=approval_id,
        approved_by=approved_by,
        approved_at=datetime.now(timezone.utc),
        dataset_id=dataset_id,
        pre_analysis_checkpoint_id=pre_analysis_checkpoint_id,
        pre_analysis_checkpoint_sha256=canonical_checkpoint_sha256(checkpoint),
        operation=operation,
        context_sha256=context_sha256,
        parameters_sha256=parameters_sha256,
        decision="approved",
    )

    approvals_dir = Path(job_dir) / "dvc_approvals"
    approvals_dir.mkdir(parents=True, exist_ok=True)
    approval_path = approvals_dir / f"{approval_id}.json"
    approval_path.write_text(
        json.dumps(approval.model_dump(mode="json"), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    audit_path = approvals_dir / f"{approval_id}.audit.json"
    audit_path.write_text(
        json.dumps(
            {
                "schema": "openscientist-dvc-approval-audit/0.1",
                "approval_id": approval_id,
                "dataset_id": dataset_id,
                "pre_analysis_checkpoint_id": pre_analysis_checkpoint_id,
                "assessment_frameworks": [
                    item.get("framework") for item in checkpoint.get("assessments", [])
                ],
                "created_via": created_via,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    workflow.record_approval(
        approval_id,
        checkpoint_id=pre_analysis_checkpoint_id,
        dataset_id=dataset_id,
        actor=approved_by,
        decision=ApprovalDecision(
            approval_id=approval_id,
            run_id=workflow.run_id,
            assay_id="dvc",
            dataset_id=dataset_id,
            operation_id=operation,
            contract_sha256=operation_contract_sha256(operation),
            context_sha256=context_sha256,
            parameters_sha256=parameters_sha256,
            decided_by=approved_by,
            decided_at=approval.approved_at,
            decision="approved",
        ),
    )
    legacy_workflow.record_approval(
        approval_id,
        checkpoint_id=pre_analysis_checkpoint_id,
        dataset_id=dataset_id,
        actor=approved_by,
    )

    return approval


def list_dvc_pre_analysis_checkpoints(job_dir: Path) -> list[dict[str, Any]]:
    """List all saved pre-analysis checkpoints with their assessment summaries and approval status."""
    assessments_dir = Path(job_dir) / "dvc_assessments"
    if not assessments_dir.is_dir():
        return []
    store = FileDVCApprovalStore(job_dir)
    existing_approvals = store.list_approvals()
    approved_checkpoint_ids = {app.pre_analysis_checkpoint_id: app for app in existing_approvals}

    checkpoints: list[dict[str, Any]] = []
    for path in sorted(assessments_dir.glob("dvc-assess-*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if payload.get("checkpoint") != "pre_analysis":
            continue
        checkpoint_id = str(payload.get("checkpoint_id") or path.stem)
        approval = approved_checkpoint_ids.get(checkpoint_id)
        checkpoints.append(
            {
                "checkpoint_id": checkpoint_id,
                "dataset_id": payload.get("dataset_id"),
                "context_sha256": payload.get("context_sha256"),
                "created_at": payload.get("created_at"),
                "assessments": payload.get("assessments", []),
                "approved": approval is not None,
                "approval_id": approval.approval_id if approval else None,
                "approved_by": approval.approved_by if approval else None,
                "approved_at": approval.approved_at.isoformat() if approval else None,
                "operation": approval.operation if approval else None,
            }
        )
    return checkpoints


def load_checkpoint_context(job_dir: Path, checkpoint_id: str) -> PreclinicalStudyContext | None:
    """Load the study context envelope associated with a pre-analysis checkpoint if present."""
    if not re.fullmatch(r"dvc-assess-[0-9a-fA-F-]{36}", checkpoint_id):
        return None
    root = (Path(job_dir) / "dvc_assessments").resolve()
    context_path = (root / f"{checkpoint_id}.context.json").resolve()
    if root not in context_path.parents or not context_path.is_file():
        return None
    try:
        payload = json.loads(context_path.read_text(encoding="utf-8"))
        return PreclinicalStudyContext.model_validate(payload)
    except Exception:
        return None
