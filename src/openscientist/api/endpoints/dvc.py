"""Authenticated DVC governance endpoints.

These routes are trusted server-side write paths. Agent MCP tools can reference
approvals but cannot create them.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from openscientist.api.auth import get_current_user_from_api_key
from openscientist.database.models import Job, User
from openscientist.database.session import get_session
from openscientist.integrations.dvc.execution import (
    DVCAnalysisApproval,
    OPERATION_CONTRACTS,
    canonical_context_sha256,
)
from openscientist.preclinical_context.models import PreclinicalStudyContext

router = APIRouter(prefix="/dvc", tags=["DVC Governance"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApprovalCreate(StrictModel):
    dataset_id: str = Field(pattern=r"^dvc-[0-9a-fA-F-]{36}$")
    operation: str = Field(min_length=1, max_length=100)
    context: PreclinicalStudyContext
    pre_analysis_checkpoint_id: str = Field(
        pattern=r"^dvc-assess-[0-9a-fA-F-]{36}$"
    )


class ApprovalResponse(StrictModel):
    approval_id: str
    job_id: str
    dataset_id: str
    operation: str
    approved_by: str
    approved_at: datetime
    context_sha256: str
    decision: Literal["approved"]
    pre_analysis_checkpoint_id: str


def _job_dir(job_id: UUID) -> Path:
    root = Path(os.getenv("OPENSCIENTIST_JOBS_DIR", "jobs")).resolve()
    path = (root / str(job_id)).resolve()
    if root not in path.parents:
        raise HTTPException(400, "Invalid job path.")
    return path


def _load_pre_analysis_checkpoint(job_dir: Path, checkpoint_id: str, dataset_id: str) -> dict:
    if not re.fullmatch(r"dvc-assess-[0-9a-fA-F-]{36}", checkpoint_id):
        raise HTTPException(400, "Invalid pre-analysis checkpoint id.")
    path = (job_dir / "dvc_assessments" / f"{checkpoint_id}.json").resolve()
    root = (job_dir / "dvc_assessments").resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(404, "Pre-analysis assessment checkpoint not found.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, "Pre-analysis assessment checkpoint is invalid.") from exc
    if payload.get("checkpoint") != "pre_analysis":
        raise HTTPException(400, "The supplied checkpoint is not a pre-analysis assessment.")
    if payload.get("dataset_id") != dataset_id:
        raise HTTPException(400, "Assessment checkpoint does not belong to this dataset.")
    return payload


@router.post("/jobs/{job_id}/approvals", response_model=ApprovalResponse)
async def create_dvc_approval(
    job_id: UUID,
    body: ApprovalCreate,
    current_user: User = Depends(get_current_user_from_api_key),
    session: AsyncSession = Depends(get_session),
) -> ApprovalResponse:
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found.")
    if getattr(job, "user_id", None) != current_user.id:
        raise HTTPException(403, "Not authorized for this job.")
    if body.operation not in OPERATION_CONTRACTS:
        raise HTTPException(400, "Operation is not governed for DVC execution.")

    job_dir = _job_dir(job_id)
    checkpoint = _load_pre_analysis_checkpoint(
        job_dir, body.pre_analysis_checkpoint_id, body.dataset_id
    )
    approval_id = f"approval-{uuid4()}"
    identity = getattr(current_user, "email", None) or str(current_user.id)
    approval = DVCAnalysisApproval(
        approval_id=approval_id,
        approved_by=identity,
        approved_at=datetime.now(timezone.utc),
        operation=body.operation,
        context_sha256=canonical_context_sha256(body.context),
    )

    approvals_dir = job_dir / "dvc_approvals"
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
                "job_id": str(job_id),
                "dataset_id": body.dataset_id,
                "pre_analysis_checkpoint_id": body.pre_analysis_checkpoint_id,
                "assessment_frameworks": [
                    item.get("framework") for item in checkpoint.get("assessments", [])
                ],
                "created_via": "authenticated_rest_api",
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return ApprovalResponse(
        job_id=str(job_id),
        dataset_id=body.dataset_id,
        pre_analysis_checkpoint_id=body.pre_analysis_checkpoint_id,
        **approval.model_dump(),
    )
