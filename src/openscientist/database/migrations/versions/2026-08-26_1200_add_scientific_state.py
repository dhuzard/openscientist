"""Persist experimental graphs, assay ledgers, and evidence object indexes.

Revision ID: add_scientific_state
Revises: add_job_guidance
Create Date: 2026-08-26 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "add_scientific_state"
down_revision: str | None = "add_job_guidance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (
    "preclinical_context_snapshots",
    "assay_run_snapshots",
    "assay_evidence_objects",
)


def _identity_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    ]


def _enable_job_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {table}_job_owner ON {table} FOR ALL
            USING (
                EXISTS (
                    SELECT 1 FROM jobs
                    WHERE jobs.id = {table}.job_id
                      AND jobs.owner_id::text =
                          current_setting('app.current_user_id', TRUE)
                )
            )
            WITH CHECK (
                EXISTS (
                    SELECT 1 FROM jobs
                    WHERE jobs.id = {table}.job_id
                      AND jobs.owner_id::text =
                          current_setting('app.current_user_id', TRUE)
                )
            )
        """
    )
    # User-facing sessions may append and query scientific records, but cannot
    # mutate or delete ledger history. Administrative maintenance remains
    # available through the separately audited BYPASSRLS role.
    op.execute(
        f"REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON {table} FROM openscientist_app"
    )
    op.execute(f"GRANT SELECT, INSERT ON {table} TO openscientist_app")
    op.execute(f"GRANT ALL ON {table} TO openscientist_admin")


def upgrade() -> None:
    op.create_table(
        "preclinical_context_snapshots",
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("study_id", sa.String(length=200), nullable=False),
        sa.Column("schema_version", sa.String(length=100), nullable=False),
        sa.Column("snapshot_version", sa.Integer(), nullable=False),
        sa.Column("graph_sha256", sa.String(length=64), nullable=False),
        sa.Column("graph", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        *_identity_columns(),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "study_id", "snapshot_version"),
        sa.UniqueConstraint("job_id", "study_id", "graph_sha256"),
    )
    op.create_index(
        op.f("ix_preclinical_context_snapshots_job_id"),
        "preclinical_context_snapshots",
        ["job_id"],
    )
    op.create_index(
        op.f("ix_preclinical_context_snapshots_study_id"),
        "preclinical_context_snapshots",
        ["study_id"],
    )

    op.create_table(
        "assay_run_snapshots",
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.String(length=200), nullable=False),
        sa.Column("study_id", sa.String(length=200), nullable=False),
        sa.Column("assay_id", sa.String(length=100), nullable=False),
        sa.Column("dataset_id", sa.String(length=200), nullable=False),
        sa.Column("operation_id", sa.String(length=100), nullable=False),
        sa.Column("stage", sa.String(length=50), nullable=False),
        sa.Column("run_version", sa.Integer(), nullable=False),
        sa.Column("state_sha256", sa.String(length=64), nullable=False),
        sa.Column("state", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        *_identity_columns(),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "run_id", "run_version"),
        sa.UniqueConstraint("job_id", "run_id", "state_sha256"),
    )
    for column in ("job_id", "run_id", "study_id", "assay_id", "dataset_id", "stage"):
        op.create_index(
            op.f(f"ix_assay_run_snapshots_{column}"),
            "assay_run_snapshots",
            [column],
        )

    op.create_table(
        "assay_evidence_objects",
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.String(length=200), nullable=False),
        sa.Column("artifact_id", sa.String(length=200), nullable=False),
        sa.Column("assay_id", sa.String(length=100), nullable=False),
        sa.Column("dataset_id", sa.String(length=200), nullable=False),
        sa.Column("role", sa.String(length=100), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.String(length=200), nullable=True),
        sa.Column("schema_id", sa.String(length=300), nullable=True),
        sa.Column("object_key", sa.Text(), nullable=False),
        *_identity_columns(),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "run_id", "artifact_id"),
    )
    for column in ("job_id", "run_id", "assay_id", "dataset_id", "role", "sha256"):
        op.create_index(
            op.f(f"ix_assay_evidence_objects_{column}"),
            "assay_evidence_objects",
            [column],
        )

    for table in _TABLES:
        _enable_job_rls(table)


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_job_owner ON {table}")
        op.drop_table(table)
