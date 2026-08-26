"""Contract-driven discovery and approval of assay analysis runs."""

from __future__ import annotations

import importlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from openscientist.assays.contracts import ApprovalDecision, AssayAdapter
from openscientist.assays.registry import AssayRegistry, get_assay_registry
from openscientist.assays.workflow import (
    AnalysisRun,
    AnalysisRunStage,
    AnalysisRunStore,
    canonical_json_sha256,
)


@dataclass(frozen=True)
class AssayReviewItem:
    adapter: AssayAdapter
    checkpoint: dict[str, Any]


def _import_callable(path: str) -> Any:
    module_name, separator, attribute = path.rpartition(".")
    if not separator:
        raise ValueError(f"Invalid review handler path: {path!r}.")
    value = getattr(importlib.import_module(module_name), attribute)
    if not callable(value):
        raise TypeError(f"Review handler is not callable: {path!r}.")
    return value


def _generic_pending_runs(job_dir: Path, adapter: AssayAdapter) -> list[dict[str, Any]]:
    pending: list[dict[str, Any]] = []
    for path in sorted((Path(job_dir) / "assay_runs").glob("*/run.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            run = AnalysisRun.model_validate(payload)
        except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
            continue
        if run.assay_id != adapter.adapter_id:
            continue
        contract = adapter.operation_contracts.get(run.operation_id)
        if contract is None or not contract.approval_required:
            continue
        pending.append(
            {
                "checkpoint_id": run.checkpoints[-1] if run.checkpoints else run.run_id,
                "run_id": run.run_id,
                "dataset_id": run.dataset_id,
                "operation_id": run.operation_id,
                "context_sha256": run.context_sha256,
                "parameters_sha256": run.parameters_sha256,
                "approved": run.current_stage
                in {
                    AnalysisRunStage.APPROVED,
                    AnalysisRunStage.ANALYZED,
                    AnalysisRunStage.POST_ASSESSED,
                    AnalysisRunStage.REPORTED,
                },
                "current_stage": run.current_stage.value,
                "evidence": [item.model_dump(mode="json") for item in run.evidence],
            }
        )
    return pending


def list_assay_reviews(
    job_dir: Path,
    *,
    registry: AssayRegistry | None = None,
) -> list[AssayReviewItem]:
    """Discover review items using adapter handlers or generic run state."""

    items: list[AssayReviewItem] = []
    for adapter in (registry or get_assay_registry()).list():
        handler_path = adapter.review_panel.approval_list_handler_path
        checkpoints = (
            _import_callable(handler_path)(job_dir)
            if handler_path
            else _generic_pending_runs(job_dir, adapter)
        )
        for checkpoint in checkpoints:
            if isinstance(checkpoint, dict):
                items.append(AssayReviewItem(adapter=adapter, checkpoint=checkpoint))
    return sorted(
        items,
        key=lambda item: (
            item.adapter.adapter_id,
            str(item.checkpoint.get("run_id") or item.checkpoint.get("checkpoint_id")),
        ),
    )


def approve_generic_run(
    *,
    job_dir: Path,
    adapter: AssayAdapter,
    checkpoint: dict[str, Any],
    decided_by: str,
    rationale: str | None = None,
) -> ApprovalDecision:
    """Bind an authenticated decision to one exact pending analysis run."""

    run_id = str(checkpoint.get("run_id") or "")
    state_path = Path(job_dir) / "assay_runs" / run_id / "run.json"
    if not state_path.is_file():
        raise FileNotFoundError(f"Analysis run not found: {run_id}")
    run = AnalysisRun.model_validate_json(state_path.read_text(encoding="utf-8"))
    if run.assay_id != adapter.adapter_id:
        raise ValueError("Analysis run does not belong to the selected assay adapter.")
    if run.current_stage != AnalysisRunStage.PENDING_APPROVAL:
        raise ValueError("Analysis run is not pending approval.")
    contract = adapter.require_operation(run.operation_id)
    if not contract.approval_required:
        raise ValueError("Operation does not require an approval decision.")
    if run.context_sha256 is None or run.parameters_sha256 is None:
        raise ValueError("Approval requires exact context and parameter hashes.")
    decision = ApprovalDecision(
        approval_id=f"approval-{uuid4()}",
        run_id=run.run_id,
        assay_id=run.assay_id,
        dataset_id=run.dataset_id,
        operation_id=run.operation_id,
        contract_sha256=canonical_json_sha256(asdict(contract)),
        context_sha256=run.context_sha256,
        parameters_sha256=run.parameters_sha256,
        decided_by=decided_by,
        decided_at=datetime.now(timezone.utc),
        decision="approved",
        rationale=rationale,
    )
    store = AnalysisRunStore(
        job_dir,
        run_id=run.run_id,
        study_id=run.study_id,
        assay_id=run.assay_id,
        dataset_id=run.dataset_id,
        operation_id=run.operation_id,
        job_id=run.job_id,
        context_sha256=run.context_sha256,
        parameters_sha256=run.parameters_sha256,
    )
    store.record_approval(
        decision.approval_id,
        checkpoint_id=run.checkpoints[-1] if run.checkpoints else run.run_id,
        dataset_id=run.dataset_id,
        actor=decided_by,
        decision=decision,
    )
    return decision


def load_assay_review_context(
    job_dir: Path,
    adapter: AssayAdapter,
    checkpoint_id: str,
) -> Any | None:
    """Load adapter-owned review context when its contract declares a loader."""

    handler_path = adapter.review_panel.context_loader_handler_path
    return None if handler_path is None else _import_callable(handler_path)(job_dir, checkpoint_id)


def create_assay_review_approval(
    *,
    job_dir: Path,
    adapter: AssayAdapter,
    checkpoint: dict[str, Any],
    operation: str,
    context: Any,
    parameters: dict[str, Any],
    approved_by: str,
    created_via: str = "web_ui",
    rationale: str | None = None,
) -> Any:
    """Create an approval through the adapter contract or generic run reducer."""

    handler_path = adapter.review_panel.approval_create_handler_path
    if handler_path is None:
        if operation != checkpoint.get("operation_id"):
            raise ValueError("A run-scoped approval cannot change its bound operation.")
        return approve_generic_run(
            job_dir=job_dir,
            adapter=adapter,
            checkpoint=checkpoint,
            decided_by=approved_by,
            rationale=rationale,
        )
    return _import_callable(handler_path)(
        job_dir=job_dir,
        dataset_id=checkpoint["dataset_id"],
        pre_analysis_checkpoint_id=checkpoint["checkpoint_id"],
        operation=operation,
        context=context,
        parameters=parameters,
        approved_by=approved_by,
        created_via=created_via,
    )
