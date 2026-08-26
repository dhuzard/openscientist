"""PostgreSQL and object-store persistence for governed scientific state."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import desc, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from openscientist.assays import AnalysisRun
from openscientist.database.models import (
    AssayEvidenceObject,
    AssayRunSnapshot,
    PreclinicalContextSnapshot,
)
from openscientist.object_store import ObjectStore, configured_object_store, content_sha256
from openscientist.preclinical_context.models import (
    PreclinicalStudyContext,
    PreclinicalStudyContextV2,
)

PreclinicalContext = PreclinicalStudyContext | PreclinicalStudyContextV2


class ScientificPersistenceError(RuntimeError):
    """Durable scientific state is missing, corrupt, or non-monotonic."""


def _canonical_json(payload: dict[str, Any]) -> bytes:
    try:
        rendered = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ScientificPersistenceError("Scientific state is not canonical JSON.") from exc
    return (rendered + "\n").encode("utf-8")


def _context_from_payload(payload: dict[str, Any]) -> PreclinicalContext:
    if payload.get("schema_version") == "openscientist-preclinical-context/0.2":
        return PreclinicalStudyContextV2.model_validate(payload)
    return PreclinicalStudyContext.model_validate(payload)


class ScientificStateRepository:
    """Append-only database index backed by immutable content-addressed objects."""

    def __init__(self, session: AsyncSession, object_store: ObjectStore) -> None:
        self.session = session
        self.object_store = object_store

    async def _lock_identity(self, identity: str) -> None:
        """Serialize append-version allocation for one scientific identity."""

        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
            {"identity": identity},
        )

    async def _put_object(
        self,
        key: str,
        content: bytes,
        digest: str,
        *,
        content_type: str,
    ) -> None:
        await asyncio.to_thread(
            self.object_store.put,
            key,
            content,
            sha256=digest,
            content_type=content_type,
        )

    async def _get_object(self, key: str, digest: str) -> bytes:
        return await asyncio.to_thread(self.object_store.get, key, sha256=digest)

    async def persist_context(
        self,
        job_id: UUID,
        context: PreclinicalContext,
    ) -> PreclinicalContextSnapshot:
        await self._lock_identity(f"context:{job_id}:{context.study_id}")
        payload = context.model_dump(mode="json")
        content = _canonical_json(payload)
        digest = content_sha256(content)
        existing = await self.session.scalar(
            select(PreclinicalContextSnapshot).where(
                PreclinicalContextSnapshot.job_id == job_id,
                PreclinicalContextSnapshot.study_id == context.study_id,
                PreclinicalContextSnapshot.graph_sha256 == digest,
            )
        )
        if existing is not None:
            await self._get_object(existing.object_key, existing.graph_sha256)
            return existing
        latest_version = await self.session.scalar(
            select(PreclinicalContextSnapshot.snapshot_version)
            .where(
                PreclinicalContextSnapshot.job_id == job_id,
                PreclinicalContextSnapshot.study_id == context.study_id,
            )
            .order_by(desc(PreclinicalContextSnapshot.snapshot_version))
            .limit(1)
        )
        version = (latest_version or 0) + 1
        object_key = f"jobs/{job_id}/contexts/{digest}.json"
        await self._put_object(
            object_key,
            content,
            digest,
            content_type="application/json",
        )
        snapshot = PreclinicalContextSnapshot(
            job_id=job_id,
            study_id=context.study_id,
            schema_version=context.schema_version,
            snapshot_version=version,
            graph_sha256=digest,
            graph=payload,
            object_key=object_key,
            byte_size=len(content),
        )
        self.session.add(snapshot)
        await self.session.flush()
        return snapshot

    async def latest_context(
        self,
        job_id: UUID,
        study_id: str,
    ) -> PreclinicalContext | None:
        snapshot = await self.session.scalar(
            select(PreclinicalContextSnapshot)
            .where(
                PreclinicalContextSnapshot.job_id == job_id,
                PreclinicalContextSnapshot.study_id == study_id,
            )
            .order_by(desc(PreclinicalContextSnapshot.snapshot_version))
            .limit(1)
        )
        if snapshot is None:
            return None
        content = await self._get_object(snapshot.object_key, snapshot.graph_sha256)
        try:
            payload = json.loads(content)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ScientificPersistenceError("Stored experimental graph is unreadable.") from exc
        if payload != snapshot.graph:
            raise ScientificPersistenceError("Database and object-store graph snapshots differ.")
        return _context_from_payload(payload)

    async def persist_analysis_run(
        self,
        job_id: UUID,
        run: AnalysisRun,
        *,
        job_dir: Path | None = None,
    ) -> AssayRunSnapshot:
        if run.job_id != str(job_id):
            raise ScientificPersistenceError("Assay run belongs to a different job.")
        await self._lock_identity(f"assay-run:{job_id}:{run.run_id}")
        payload = run.model_dump(mode="json")
        content = _canonical_json(payload)
        digest = content_sha256(content)
        same_version = await self.session.scalar(
            select(AssayRunSnapshot).where(
                AssayRunSnapshot.job_id == job_id,
                AssayRunSnapshot.run_id == run.run_id,
                AssayRunSnapshot.run_version == run.version,
            )
        )
        if same_version is not None:
            if same_version.state_sha256 != digest:
                raise ScientificPersistenceError(
                    "An assay-run version cannot be replaced with different state."
                )
            await self._get_object(same_version.object_key, digest)
            await self._persist_evidence(job_id, run, job_dir=job_dir)
            return same_version
        latest = await self.session.scalar(
            select(AssayRunSnapshot)
            .where(
                AssayRunSnapshot.job_id == job_id,
                AssayRunSnapshot.run_id == run.run_id,
            )
            .order_by(desc(AssayRunSnapshot.run_version))
            .limit(1)
        )
        if latest is not None and run.version < latest.run_version:
            raise ScientificPersistenceError("Assay-run snapshots must advance monotonically.")
        object_key = f"jobs/{job_id}/runs/{digest}.json"
        await self._put_object(
            object_key,
            content,
            digest,
            content_type="application/json",
        )
        snapshot = AssayRunSnapshot(
            job_id=job_id,
            run_id=run.run_id,
            study_id=run.study_id,
            assay_id=run.assay_id,
            dataset_id=run.dataset_id,
            operation_id=run.operation_id,
            stage=run.current_stage.value,
            run_version=run.version,
            state_sha256=digest,
            state=payload,
            object_key=object_key,
            byte_size=len(content),
        )
        self.session.add(snapshot)
        await self.session.flush()
        await self._persist_evidence(job_id, run, job_dir=job_dir)
        return snapshot

    async def latest_analysis_run(self, job_id: UUID, run_id: str) -> AnalysisRun | None:
        snapshot = await self.session.scalar(
            select(AssayRunSnapshot)
            .where(
                AssayRunSnapshot.job_id == job_id,
                AssayRunSnapshot.run_id == run_id,
            )
            .order_by(desc(AssayRunSnapshot.run_version))
            .limit(1)
        )
        if snapshot is None:
            return None
        content = await self._get_object(snapshot.object_key, snapshot.state_sha256)
        try:
            payload = json.loads(content)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ScientificPersistenceError("Stored assay-run state is unreadable.") from exc
        if payload != snapshot.state:
            raise ScientificPersistenceError("Database and object-store run snapshots differ.")
        return AnalysisRun.model_validate(payload)

    async def restore_latest_context(
        self,
        job_id: UUID,
        study_id: str,
        target: Path,
    ) -> PreclinicalContext | None:
        """Restore the latest verified graph to the existing JSON file interface."""

        context = await self.latest_context(job_id, study_id)
        if context is None:
            return None
        _atomic_write(target, _canonical_json(context.model_dump(mode="json")))
        return context

    async def restore_latest_analysis_run(
        self,
        job_id: UUID,
        run_id: str,
        target: Path,
    ) -> AnalysisRun | None:
        """Restore the latest verified run ledger to the existing JSON interface."""

        run = await self.latest_analysis_run(job_id, run_id)
        if run is None:
            return None
        _atomic_write(target, _canonical_json(run.model_dump(mode="json")))
        return run

    async def _persist_evidence(
        self,
        job_id: UUID,
        run: AnalysisRun,
        *,
        job_dir: Path | None,
    ) -> None:
        if not run.evidence or job_dir is None:
            return
        root = Path(job_dir).resolve()
        for artifact in run.evidence:
            existing = await self.session.scalar(
                select(AssayEvidenceObject).where(
                    AssayEvidenceObject.job_id == job_id,
                    AssayEvidenceObject.run_id == run.run_id,
                    AssayEvidenceObject.artifact_id == artifact.artifact_id,
                )
            )
            path = (root / artifact.relative_path).resolve()
            if root not in path.parents or not path.is_file() or path.is_symlink():
                raise ScientificPersistenceError(
                    f"Evidence file is unavailable or unsafe: {artifact.relative_path}"
                )
            content = path.read_bytes()
            if content_sha256(content) != artifact.sha256 or len(content) != artifact.bytes:
                raise ScientificPersistenceError(
                    f"Evidence file does not match its ledger identity: {artifact.artifact_id}"
                )
            object_key = f"jobs/{job_id}/evidence/{run.assay_id}/{artifact.sha256}"
            await self._put_object(
                object_key,
                content,
                artifact.sha256,
                content_type=artifact.media_type or "application/octet-stream",
            )
            if existing is not None:
                immutable = (
                    existing.assay_id,
                    existing.dataset_id,
                    existing.role,
                    existing.sha256,
                    existing.byte_size,
                    existing.object_key,
                    existing.relative_path,
                    existing.media_type,
                    existing.schema_id,
                )
                expected = (
                    artifact.assay_id,
                    artifact.dataset_id,
                    artifact.role,
                    artifact.sha256,
                    artifact.bytes,
                    object_key,
                    artifact.relative_path,
                    artifact.media_type,
                    artifact.schema_id,
                )
                if immutable != expected:
                    raise ScientificPersistenceError(
                        f"Evidence object changed after indexing: {artifact.artifact_id}"
                    )
                continue
            self.session.add(
                AssayEvidenceObject(
                    job_id=job_id,
                    run_id=run.run_id,
                    artifact_id=artifact.artifact_id,
                    assay_id=artifact.assay_id,
                    dataset_id=artifact.dataset_id,
                    role=artifact.role,
                    relative_path=artifact.relative_path,
                    sha256=artifact.sha256,
                    byte_size=artifact.bytes,
                    media_type=artifact.media_type,
                    schema_id=artifact.schema_id,
                    object_key=object_key,
                )
            )
        await self.session.flush()


def _atomic_write(target: Path, content: bytes) -> None:
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.restore.tmp")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _context_paths(job_dir: Path) -> list[Path]:
    candidates = [job_dir / "preclinical_context.json"]
    candidates.extend((job_dir / "preclinical_contexts").glob("*.json"))
    candidates.extend((job_dir / "dvc_assessments").glob("*.context.json"))
    return _safe_job_files(job_dir, candidates)


def _safe_job_files(job_dir: Path, candidates: list[Path]) -> list[Path]:
    root = job_dir.resolve()
    safe: set[Path] = set()
    for path in candidates:
        if not path.exists():
            continue
        resolved = path.resolve()
        if path.is_symlink() or not path.is_file() or root not in resolved.parents:
            raise ScientificPersistenceError(f"Scientific state file is unsafe: {path}")
        safe.add(resolved)
    return sorted(safe)


async def persist_job_scientific_state(
    job_id: str,
    job_dir: Path,
    *,
    session: AsyncSession | None = None,
    object_store: ObjectStore | None = None,
) -> None:
    """Mirror file-compatible graph/run state into durable database/object storage."""

    try:
        job_uuid = UUID(job_id)
    except ValueError as exc:
        raise ScientificPersistenceError("Persistent jobs require a UUID job id.") from exc
    if session is None:
        from openscientist.database.session import get_admin_session

        async with get_admin_session() as managed_session:
            await persist_job_scientific_state(
                job_id,
                job_dir,
                session=managed_session,
                object_store=object_store,
            )
            await managed_session.commit()
        return
    repository = ScientificStateRepository(session, object_store or configured_object_store())
    job_dir = Path(job_dir)
    for path in _context_paths(job_dir):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            context = _context_from_payload(payload)
        except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ScientificPersistenceError(f"Invalid context snapshot: {path.name}") from exc
        await repository.persist_context(job_uuid, context)
    run_candidates = list((job_dir / "assay_runs").glob("*/run.json"))
    for path in _safe_job_files(job_dir, run_candidates):
        try:
            run = AnalysisRun.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise ScientificPersistenceError(f"Invalid assay-run snapshot: {path}") from exc
        await repository.persist_analysis_run(job_uuid, run, job_dir=job_dir)
