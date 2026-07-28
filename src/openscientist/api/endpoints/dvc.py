"""Authenticated DVC governance endpoints.

These routes are trusted server-side write paths. Agent MCP tools can reference
approvals but cannot create them.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from openscientist.api.auth import get_current_user_from_api_key
from openscientist.database.models import Job, User
from openscientist.database.session import get_session
from openscientist.integrations.dvc.execution import (
    DVCAnalysisApproval,
    canonical_context_sha256,
)
from openscientist.preclinical_context.models import PreclinicalStudyContext


router = APIRouter(prefix="/dvc", tags=["DVC Governance"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApprovalCreate(StrictModel):
    operation: str = Field(min_length=1, max_length=100)
    context: PreclinicalStudyContext


class ApprovalResponse(StrictModel):
    approval_id: str
    job_id: str
    operation: str
    approved_by: str
    approved_at: datetime
    context_sha256: str


def _job_dir(job_id: UUID) -> Path:
    root = Path(os.getenv("OPENSCIENTIST_JOBS_DIR", "jobs")).resolve()
    path = (root / str(job_id)).resolve()
    if root not in path.parents:
        raise HTTPException(400, "Invalid job path.")
    return path


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

    approval_id = f"approval-{uuid4()}"
    identity = current_user.email or str(current_user.id)
    approval = DVCAnalysisApproval(
        approval_id=approval_id,
        approved_by=identity,
        approved_at=datetime.now(timezone.utc),
        operation=body.operation,
        context_sha256=canonical_context_sha256(body.context),
    )
    target = _job_dir(job_id) / "dvc_approvals" / f"{approval_id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **approval.model_dump(mode="json"),
        "job_id": str(job_id),
        "created_via": "authenticated_rest_api",
    }
    target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return ApprovalResponse(job_id=str(job_id), **approval.model_dump())
