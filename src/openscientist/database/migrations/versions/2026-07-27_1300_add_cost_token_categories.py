"""Merge the job-skill and upstream token-bucket migration branches.

Revision ID: add_cost_token_categories
Revises: add_job_skill_assignments, add_token_buckets
Create Date: 2026-07-27 13:00:00

"""

from collections.abc import Sequence

revision: str = "add_cost_token_categories"
down_revision: tuple[str, str] = ("add_job_skill_assignments", "add_token_buckets")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Columns are created by the upstream ``add_token_buckets`` parent.

    Deployments that previously ran this revision already have the same three
    columns, so changing it into a merge point is backward-compatible while
    preventing duplicate ``ADD COLUMN`` operations on fresh databases.
    """


def downgrade() -> None:
    """The upstream parent owns the token columns and removes them."""
