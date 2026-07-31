"""Persist explicit per-job skill assignments.

Revision ID: add_job_skill_assignments
Revises: add_pubmed_mirror
Create Date: 2026-07-27 12:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "add_job_skill_assignments"
down_revision: str | None = "add_pubmed_mirror"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable assignment snapshot to jobs.

    NULL intentionally preserves the historic behavior where every enabled
    skill is made available. An explicit empty JSON array means no skills.
    """
    op.add_column(
        "jobs",
        sa.Column(
            "assigned_skill_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment=(
                "Skill UUIDs assigned to this job; NULL means all enabled skills "
                "(legacy/default), [] means no skills"
            ),
        ),
    )


def downgrade() -> None:
    """Remove per-job skill assignments."""
    op.drop_column("jobs", "assigned_skill_ids")
