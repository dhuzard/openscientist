"""Persist cache and reasoning token categories on cost records.

Revision ID: add_cost_token_categories
Revises: add_job_skill_assignments
Create Date: 2026-07-27 13:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "add_cost_token_categories"
down_revision: str | None = "add_job_skill_assignments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add additive token categories, preserving existing records as zero."""
    for column_name, comment in (
        ("cache_write_tokens", "Tokens written to a provider-side prompt cache"),
        ("cache_read_tokens", "Tokens served from a provider-side prompt cache"),
        ("reasoning_tokens", "Internal non-visible reasoning tokens"),
    ):
        op.add_column(
            "cost_records",
            sa.Column(
                column_name,
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
                comment=comment,
            ),
        )


def downgrade() -> None:
    """Remove the additional token categories."""
    op.drop_column("cost_records", "reasoning_tokens")
    op.drop_column("cost_records", "cache_read_tokens")
    op.drop_column("cost_records", "cache_write_tokens")
