"""Focused tests for queued job guidance persistence and service rules."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openscientist.database.models import Job, JobGuidance, User
from openscientist.job_guidance import (
    MAX_JOB_GUIDANCE_LENGTH,
    MAX_PENDING_JOB_GUIDANCE,
    JobGuidanceNotFoundError,
    JobGuidancePermissionError,
    JobGuidanceUnavailableError,
    JobGuidanceValidationError,
    has_pending_job_guidance,
    list_pending_job_guidance,
    mark_job_guidance_delivered,
    queue_job_guidance,
)
from tests.helpers import fake_admin_session


async def _running_job(
    session: AsyncSession,
    owner: User,
    *,
    current_iteration: int = 2,
    max_iterations: int = 5,
) -> Job:
    job = Job(
        owner_id=owner.id,
        research_question="Test queued guidance",
        status="running",
        current_iteration=current_iteration,
        max_iterations=max_iterations,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


@pytest.mark.asyncio
async def test_queue_guidance_records_owner_and_active_iteration(
    db_session: AsyncSession,
    test_user: User,
) -> None:
    job = await _running_job(db_session, test_user)

    item = await queue_job_guidance(
        job.id,
        test_user.id,
        "  Check the activity transition around lights-off.  ",
        admin_session_factory=fake_admin_session(db_session),
    )

    row = (
        await db_session.execute(select(JobGuidance).where(JobGuidance.id == item.id))
    ).scalar_one()
    assert item.content == "Check the activity transition around lights-off."
    assert item.submitted_during_iteration == 2
    assert row.author_id == test_user.id
    assert row.job_id == job.id
    assert row.delivered_at is None
    assert row.delivered_iteration is None

    await db_session.refresh(job, ["guidance"])
    assert [guidance.id for guidance in job.guidance] == [item.id]


@pytest.mark.asyncio
async def test_queue_guidance_enforces_owner_running_and_non_final_iteration(
    db_session: AsyncSession,
    test_user: User,
    test_user2: User,
) -> None:
    job = await _running_job(db_session, test_user)
    factory = fake_admin_session(db_session)

    with pytest.raises(JobGuidancePermissionError, match="Only the job owner"):
        await queue_job_guidance(
            job.id,
            test_user2.id,
            "Try another normalization",
            admin_session_factory=factory,
        )

    job.status = "paused"
    await db_session.commit()
    with pytest.raises(JobGuidanceUnavailableError, match="only be added while"):
        await queue_job_guidance(
            job.id,
            test_user.id,
            "Try another normalization",
            admin_session_factory=factory,
        )

    job.status = "running"
    job.current_iteration = job.max_iterations
    await db_session.commit()
    with pytest.raises(JobGuidanceUnavailableError, match="final iteration"):
        await queue_job_guidance(
            job.id,
            test_user.id,
            "Try another normalization",
            admin_session_factory=factory,
        )


@pytest.mark.asyncio
async def test_queue_guidance_rejects_missing_job_and_blank_content(
    db_session: AsyncSession,
    test_user: User,
) -> None:
    factory = fake_admin_session(db_session)

    with pytest.raises(JobGuidanceValidationError, match="Enter an idea"):
        await queue_job_guidance(
            uuid4(),
            test_user.id,
            " \n ",
            admin_session_factory=factory,
        )

    with pytest.raises(JobGuidanceValidationError, match="4000 characters or fewer"):
        await queue_job_guidance(
            uuid4(),
            test_user.id,
            "x" * (MAX_JOB_GUIDANCE_LENGTH + 1),
            admin_session_factory=factory,
        )

    with pytest.raises(JobGuidanceNotFoundError, match="Job not found"):
        await queue_job_guidance(
            uuid4(),
            test_user.id,
            "Use a robust regression",
            admin_session_factory=factory,
        )


@pytest.mark.asyncio
async def test_pending_queue_is_fifo_and_only_frozen_ids_are_delivered(
    db_session: AsyncSession,
    test_user: User,
) -> None:
    job = await _running_job(db_session, test_user)
    factory = fake_admin_session(db_session)
    created = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
    first = JobGuidance(
        job_id=job.id,
        author_id=test_user.id,
        content="First idea",
        submitted_during_iteration=2,
        created_at=created,
    )
    second = JobGuidance(
        job_id=job.id,
        author_id=test_user.id,
        content="Second idea",
        submitted_during_iteration=2,
        created_at=created + timedelta(seconds=1),
    )
    db_session.add_all([second, first])
    await db_session.commit()
    await db_session.refresh(first)
    await db_session.refresh(second)

    assert await has_pending_job_guidance(job.id, admin_session_factory=factory)
    frozen = await list_pending_job_guidance(job.id, admin_session_factory=factory)
    assert [item.content for item in frozen] == ["First idea", "Second idea"]

    late = await queue_job_guidance(
        job.id,
        test_user.id,
        "Late idea",
        admin_session_factory=factory,
    )
    delivered = await mark_job_guidance_delivered(
        job.id,
        [item.id for item in frozen],
        2,
        admin_session_factory=factory,
    )

    assert delivered == 2
    pending = await list_pending_job_guidance(job.id, admin_session_factory=factory)
    assert [item.id for item in pending] == [late.id]

    delivered_rows = (
        (
            await db_session.execute(
                select(JobGuidance).where(JobGuidance.id.in_([first.id, second.id]))
            )
        )
        .scalars()
        .all()
    )
    assert all(row.delivered_at is not None for row in delivered_rows)
    assert {row.delivered_iteration for row in delivered_rows} == {2}

    assert (
        await mark_job_guidance_delivered(
            job.id,
            [late.id],
            3,
            admin_session_factory=factory,
        )
        == 1
    )
    assert not await has_pending_job_guidance(job.id, admin_session_factory=factory)


@pytest.mark.asyncio
async def test_queue_guidance_bounds_pending_prompt_load(
    db_session: AsyncSession,
    test_user: User,
) -> None:
    job = await _running_job(db_session, test_user)
    db_session.add_all(
        [
            JobGuidance(
                job_id=job.id,
                author_id=test_user.id,
                content=f"Idea {index}",
                submitted_during_iteration=job.current_iteration,
            )
            for index in range(MAX_PENDING_JOB_GUIDANCE)
        ]
    )
    await db_session.commit()

    with pytest.raises(JobGuidanceUnavailableError, match="20 pending ideas"):
        await queue_job_guidance(
            job.id,
            test_user.id,
            "One idea too many",
            admin_session_factory=fake_admin_session(db_session),
        )


@pytest.mark.asyncio
async def test_mark_delivery_validates_iteration_without_opening_a_session() -> None:
    with pytest.raises(JobGuidanceValidationError, match="cannot be negative"):
        await mark_job_guidance_delivered(uuid4(), [uuid4()], -1)
