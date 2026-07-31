"""Queued user guidance for an actively running job."""

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base, UUIDv7Mixin

if TYPE_CHECKING:
    from .job import Job


class JobGuidance(UUIDv7Mixin, Base):
    """An owner-submitted idea waiting to be included in a future agent turn."""

    __tablename__ = "job_guidance"

    job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Job that should receive this guidance",
    )

    author_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="Owner who submitted this guidance",
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Owner-provided idea to include in the running investigation",
    )

    submitted_during_iteration: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Job iteration that was active when the guidance was queued",
    )

    delivered_iteration: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Iteration whose completed turn received this guidance",
    )

    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When the frozen guidance item was marked delivered",
    )

    job: Mapped["Job"] = relationship(back_populates="guidance")

    def __repr__(self) -> str:
        preview = self.content[:50] + ("..." if len(self.content) > 50 else "")
        return (
            f"<JobGuidance(id={self.id}, job_id={self.job_id}, "
            f"author_id={self.author_id}, "
            f"submitted_during_iteration={self.submitted_during_iteration}, "
            f"content={preview!r})>"
        )
