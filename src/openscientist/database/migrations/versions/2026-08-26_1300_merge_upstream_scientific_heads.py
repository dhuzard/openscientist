"""Merge upstream runtime provenance with governed scientific state.

Revision ID: merge_upstream_science
Revises: add_scientific_state, add_version_info
Create Date: 2026-08-26 13:00:00
"""

from collections.abc import Sequence

revision: str = "merge_upstream_science"
down_revision: tuple[str, str] = ("add_scientific_state", "add_version_info")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Join the two fully applied schema branches."""


def downgrade() -> None:
    """Split back to the two parent heads without changing schema."""
