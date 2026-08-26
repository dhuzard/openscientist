"""Durable snapshots and object indexes for governed scientific state."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, UUIDv7Mixin


class PreclinicalContextSnapshot(UUIDv7Mixin, Base):
    """Immutable version of one study's typed experimental graph."""

    __tablename__ = "preclinical_context_snapshots"
    __table_args__ = (
        UniqueConstraint("job_id", "study_id", "snapshot_version"),
        UniqueConstraint("job_id", "study_id", "graph_sha256"),
    )

    job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), index=True
    )
    study_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    schema_version: Mapped[str] = mapped_column(String(100), nullable=False)
    snapshot_version: Mapped[int] = mapped_column(Integer, nullable=False)
    graph_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    graph: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)


class AssayRunSnapshot(UUIDv7Mixin, Base):
    """Append-only snapshot of one hash-chained assay run ledger."""

    __tablename__ = "assay_run_snapshots"
    __table_args__ = (
        UniqueConstraint("job_id", "run_id", "run_version"),
        UniqueConstraint("job_id", "run_id", "state_sha256"),
    )

    job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    study_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    assay_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    dataset_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    operation_id: Mapped[str] = mapped_column(String(100), nullable=False)
    stage: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    run_version: Mapped[int] = mapped_column(Integer, nullable=False)
    state_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)


class AssayEvidenceObject(UUIDv7Mixin, Base):
    """Database index for a content-addressed assay evidence object."""

    __tablename__ = "assay_evidence_objects"
    __table_args__ = (UniqueConstraint("job_id", "run_id", "artifact_id"),)

    job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    artifact_id: Mapped[str] = mapped_column(String(200), nullable=False)
    assay_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    dataset_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    media_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    schema_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
