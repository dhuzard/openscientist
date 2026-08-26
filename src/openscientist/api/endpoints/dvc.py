"""Authenticated DVC governance endpoints.

These routes are trusted server-side write paths. Agent MCP tools can reference
approvals but cannot create them.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from openscientist.api.auth import get_current_user_from_api_key
from openscientist.database.models import Job, User
from openscientist.database.rls import set_current_user
from openscientist.database.session import get_session
from openscientist.integrations.dvc.workflow import (
    DVCWorkflowConflictError,
    DVCWorkflowCorruptError,
)
from openscientist.preclinical_context.models import PreclinicalStudyContext

router = APIRouter(prefix="/dvc", tags=["DVC Governance"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApprovalCreate(StrictModel):
    dataset_id: str = Field(pattern=r"^dvc-[0-9a-fA-F-]{36}$")
    operation: str = Field(min_length=1, max_length=100)
    context: PreclinicalStudyContext
    parameters: dict[str, Any] = Field(default_factory=dict)
    pre_analysis_checkpoint_id: str = Field(pattern=r"^dvc-assess-[0-9a-fA-F-]{36}$")


class ApprovalResponse(StrictModel):
    approval_id: str
    job_id: str
    dataset_id: str
    operation: str
    approved_by: str
    approved_at: datetime
    context_sha256: str
    parameters_sha256: str
    decision: Literal["approved"]
    pre_analysis_checkpoint_id: str
    pre_analysis_checkpoint_sha256: str


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
    current_user: Annotated[User, Depends(get_current_user_from_api_key)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ApprovalResponse:
    from openscientist.integrations.dvc.approvals import create_dvc_approval_record

    await set_current_user(session, current_user.id)
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found.")
    if job.owner_id != current_user.id:
        raise HTTPException(403, "Not authorized for this job.")

    job_dir = _job_dir(job_id)
    identity = getattr(current_user, "email", None) or str(current_user.id)
    try:
        approval = create_dvc_approval_record(
            job_dir=job_dir,
            dataset_id=body.dataset_id,
            pre_analysis_checkpoint_id=body.pre_analysis_checkpoint_id,
            operation=body.operation,
            context=body.context,
            parameters=body.parameters,
            approved_by=identity,
            created_via="authenticated_rest_api",
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (DVCWorkflowConflictError, DVCWorkflowCorruptError) as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    return ApprovalResponse(
        job_id=str(job_id),
        **approval.model_dump(),
    )
