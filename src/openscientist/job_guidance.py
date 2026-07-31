"""Persistence service for owner-submitted ideas queued during a running job."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from openscientist.database.models import Job, JobGuidance
from openscientist.database.session import get_admin_session
from openscientist.job.types import JobStatus

AdminSessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
MAX_JOB_GUIDANCE_LENGTH = 4000
MAX_PENDING_JOB_GUIDANCE = 20


class JobGuidanceError(ValueError):
    """Base class for expected, user-safe guidance queue errors."""


class JobGuidanceValidationError(JobGuidanceError):
    """The submitted queue data is invalid."""


class JobGuidanceNotFoundError(JobGuidanceError):
    """The requested job does not exist."""


class JobGuidancePermissionError(JobGuidanceError):
    """The requester does not own the requested job."""


class JobGuidanceUnavailableError(JobGuidanceError):
    """The job is not at a point where guidance can be queued."""


@dataclass(frozen=True, slots=True)
class JobGuidanceItem:
    """Immutable guidance snapshot suitable for freezing into an agent turn."""

    id: UUID
    content: str
    submitted_during_iteration: int
    created_at: datetime


def _as_uuid(value: str | UUID, field_name: str) -> UUID:
    """Normalize a service UUID argument and raise a user-safe error."""
    if isinstance(value, UUID):
        return value
    try:
        return UUID(value)
    except (TypeError, ValueError, AttributeError) as exc:
        raise JobGuidanceValidationError(f"Invalid {field_name}") from exc


def _to_item(guidance: JobGuidance) -> JobGuidanceItem:
    """Copy an ORM row into an immutable service value."""
    return JobGuidanceItem(
        id=guidance.id,
        content=guidance.content,
        submitted_during_iteration=guidance.submitted_during_iteration,
        created_at=guidance.created_at,
    )


async def queue_job_guidance(
    job_id: str | UUID,
    owner_id: str | UUID,
    content: str,
    *,
    admin_session_factory: AdminSessionFactory = get_admin_session,
) -> JobGuidanceItem:
    """Queue an idea for an owned, running job before its final iteration."""
    job_uuid = _as_uuid(job_id, "job ID")
    owner_uuid = _as_uuid(owner_id, "owner ID")
    normalized_content = content.strip()
    if not normalized_content:
        raise JobGuidanceValidationError("Enter an idea before adding it")
    if len(normalized_content) > MAX_JOB_GUIDANCE_LENGTH:
        raise JobGuidanceValidationError(
            f"Ideas must be {MAX_JOB_GUIDANCE_LENGTH} characters or fewer"
        )

    async with admin_session_factory() as session:
        result = await session.execute(select(Job).where(Job.id == job_uuid).with_for_update())
        job = result.scalar_one_or_none()
        if job is None:
            raise JobGuidanceNotFoundError("Job not found")
        if job.owner_id != owner_uuid:
            raise JobGuidancePermissionError("Only the job owner can add ideas")
        if job.status != JobStatus.RUNNING:
            raise JobGuidanceUnavailableError("Ideas can only be added while the job is running")
        if job.current_iteration >= job.max_iterations:
            raise JobGuidanceUnavailableError("The job is already on its final iteration")

        pending_count = await session.scalar(
            select(func.count(JobGuidance.id)).where(
                JobGuidance.job_id == job.id,
                JobGuidance.delivered_at.is_(None),
            )
        )
        if int(pending_count or 0) >= MAX_PENDING_JOB_GUIDANCE:
            raise JobGuidanceUnavailableError(
                f"This job already has {MAX_PENDING_JOB_GUIDANCE} pending ideas"
            )

        guidance = JobGuidance(
            job_id=job.id,
            author_id=owner_uuid,
            content=normalized_content,
            submitted_during_iteration=job.current_iteration,
        )
        session.add(guidance)
        await session.commit()
        await session.refresh(guidance)
        return _to_item(guidance)


async def list_pending_job_guidance(
    job_id: str | UUID,
    *,
    admin_session_factory: AdminSessionFactory = get_admin_session,
) -> list[JobGuidanceItem]:
    """Return pending guidance in stable FIFO order through an admin session."""
    job_uuid = _as_uuid(job_id, "job ID")
    async with admin_session_factory() as session:
        result = await session.execute(
            select(JobGuidance)
            .where(
                JobGuidance.job_id == job_uuid,
                JobGuidance.delivered_at.is_(None),
            )
            .order_by(JobGuidance.created_at, JobGuidance.id)
        )
        return [_to_item(row) for row in result.scalars().all()]


async def has_pending_job_guidance(
    job_id: str | UUID,
    *,
    admin_session_factory: AdminSessionFactory = get_admin_session,
) -> bool:
    """Return whether a job has at least one pending guidance item."""
    job_uuid = _as_uuid(job_id, "job ID")
    pending = exists().where(
        JobGuidance.job_id == job_uuid,
        JobGuidance.delivered_at.is_(None),
    )
    async with admin_session_factory() as session:
        return bool(await session.scalar(select(pending)))


async def mark_job_guidance_delivered(
    job_id: str | UUID,
    guidance_ids: Iterable[str | UUID],
    delivered_iteration: int,
    *,
    admin_session_factory: AdminSessionFactory = get_admin_session,
) -> int:
    """Mark only the frozen guidance IDs as delivered after a completed turn."""
    job_uuid = _as_uuid(job_id, "job ID")
    if delivered_iteration < 0:
        raise JobGuidanceValidationError("Delivered iteration cannot be negative")

    frozen_ids = tuple(_as_uuid(item_id, "guidance ID") for item_id in guidance_ids)
    if not frozen_ids:
        return 0

    now = func.now()
    async with admin_session_factory() as session:
        result = await session.execute(
            update(JobGuidance)
            .where(
                JobGuidance.job_id == job_uuid,
                JobGuidance.id.in_(frozen_ids),
                JobGuidance.delivered_at.is_(None),
            )
            .values(
                delivered_at=now,
                delivered_iteration=delivered_iteration,
                updated_at=now,
            )
            .returning(JobGuidance.id)
        )
        delivered_count = len(result.scalars().all())
        await session.commit()
        return delivered_count


__all__ = [
    "JobGuidanceError",
    "JobGuidanceItem",
    "JobGuidanceNotFoundError",
    "JobGuidancePermissionError",
    "JobGuidanceUnavailableError",
    "JobGuidanceValidationError",
    "MAX_JOB_GUIDANCE_LENGTH",
    "MAX_PENDING_JOB_GUIDANCE",
    "has_pending_job_guidance",
    "list_pending_job_guidance",
    "mark_job_guidance_delivered",
    "queue_job_guidance",
]
