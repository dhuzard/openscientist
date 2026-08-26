from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from openscientist.assays import AnalysisRunStore, EvidenceArtifact
from openscientist.database.models import (
    AssayEvidenceObject,
    AssayRunSnapshot,
    Job,
    PreclinicalContextSnapshot,
)
from openscientist.object_store import FilesystemObjectStore, content_sha256
from openscientist.preclinical_context import PreclinicalStudyContextV2, StudyNode
from openscientist.scientific_persistence import (
    ScientificPersistenceError,
    ScientificStateRepository,
    persist_job_scientific_state,
)


async def test_context_snapshots_are_idempotent_versioned_and_recoverable(
    db_session: AsyncSession,
    test_job: Job,
    tmp_path,
) -> None:
    object_store = FilesystemObjectStore(tmp_path / "objects")
    repository = ScientificStateRepository(db_session, object_store)
    context = PreclinicalStudyContextV2(study=StudyNode(study_id="study-1"))

    first = await repository.persist_context(test_job.id, context)
    retried = await repository.persist_context(test_job.id, context)

    assert first.id == retried.id
    assert first.snapshot_version == 1
    assert await repository.latest_context(test_job.id, "study-1") == context
    assert (
        await db_session.scalar(select(func.count()).select_from(PreclinicalContextSnapshot)) == 1
    )

    target = tmp_path / "restored" / "preclinical_context.json"
    restored = await repository.restore_latest_context(test_job.id, "study-1", target)
    assert restored == context
    assert PreclinicalStudyContextV2.model_validate_json(target.read_text()) == context


async def test_run_ledger_and_evidence_are_append_only_and_recoverable(
    db_session: AsyncSession,
    test_job: Job,
    tmp_path,
) -> None:
    job_dir = tmp_path / str(test_job.id)
    store = AnalysisRunStore.for_analysis(
        job_dir,
        study_id="study-1",
        assay_id="open_field",
        dataset_id="tracking-1",
        operation_id="summarize_tracking",
        context_sha256="a" * 64,
        parameters_sha256="b" * 64,
        job_id=str(test_job.id),
    )
    store.record_dataset("tracking-1")
    store.record_checkpoint("pre-1", is_pre=True, context_sha256="a" * 64)
    version_three = store.record_execution(
        "execution-1",
        dataset_id="tracking-1",
        operation="summarize_tracking",
    )

    object_store = FilesystemObjectStore(tmp_path / "objects")
    repository = ScientificStateRepository(db_session, object_store)
    first = await repository.persist_analysis_run(test_job.id, version_three, job_dir=job_dir)

    evidence_path = job_dir / "outputs" / "summary.json"
    evidence_path.parent.mkdir(parents=True)
    evidence_content = b'{"distance_cm":123.4}\n'
    evidence_path.write_bytes(evidence_content)
    version_four = store.record_evidence(
        EvidenceArtifact(
            artifact_id="summary-1",
            run_id=store.run_id,
            assay_id="open_field",
            dataset_id="tracking-1",
            role="summary",
            relative_path="outputs/summary.json",
            sha256=content_sha256(evidence_content),
            bytes=len(evidence_content),
            media_type="application/json",
        )
    )
    second = await repository.persist_analysis_run(test_job.id, version_four, job_dir=job_dir)
    retried = await repository.persist_analysis_run(test_job.id, version_four, job_dir=job_dir)

    assert first.run_version == 3
    assert second.run_version == 4
    assert retried.id == second.id
    assert await db_session.scalar(select(func.count()).select_from(AssayRunSnapshot)) == 2
    assert await db_session.scalar(select(func.count()).select_from(AssayEvidenceObject)) == 1
    assert await repository.latest_analysis_run(test_job.id, store.run_id) == version_four

    target = tmp_path / "restored" / "run.json"
    restored = await repository.restore_latest_analysis_run(test_job.id, store.run_id, target)
    assert restored == version_four
    assert target.is_file()


async def test_run_ledger_rejects_cross_job_state(
    db_session: AsyncSession,
    test_job: Job,
    tmp_path,
) -> None:
    store = AnalysisRunStore.for_analysis(
        tmp_path,
        study_id="study-1",
        assay_id="dvc",
        dataset_id="dvc-1",
        operation_id="check_data_sanity",
        context_sha256="a" * 64,
        parameters_sha256="b" * 64,
        job_id=str(uuid4()),
    )
    repository = ScientificStateRepository(
        db_session,
        FilesystemObjectStore(tmp_path / "objects"),
    )

    with pytest.raises(ScientificPersistenceError, match="different job"):
        await repository.persist_analysis_run(test_job.id, store.load())


async def test_job_sync_discovers_canonical_context_and_run_files(
    db_session: AsyncSession,
    test_job: Job,
    tmp_path,
) -> None:
    job_dir = tmp_path / str(test_job.id)
    job_dir.mkdir()
    context = PreclinicalStudyContextV2(study=StudyNode(study_id="study-sync"))
    (job_dir / "preclinical_context.json").write_text(context.model_dump_json())
    store = AnalysisRunStore.for_analysis(
        job_dir,
        study_id="study-sync",
        assay_id="dvc",
        dataset_id="dvc-sync",
        operation_id="check_data_sanity",
        context_sha256="a" * 64,
        parameters_sha256="b" * 64,
        job_id=str(test_job.id),
    )
    store.record_dataset("dvc-sync")

    await persist_job_scientific_state(
        str(test_job.id),
        job_dir,
        session=db_session,
        object_store=FilesystemObjectStore(tmp_path / "objects"),
    )

    assert (
        await db_session.scalar(select(func.count()).select_from(PreclinicalContextSnapshot)) == 1
    )
    assert await db_session.scalar(select(func.count()).select_from(AssayRunSnapshot)) == 1
    assert store.state_file.is_file()


async def test_scientific_tables_are_rls_protected_and_app_append_only(
    db_session: AsyncSession,
) -> None:
    tables = (
        "preclinical_context_snapshots",
        "assay_run_snapshots",
        "assay_evidence_objects",
    )
    for table in tables:
        rls = await db_session.scalar(
            text("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE relname = :t"),
            {"t": table},
        )
        can_update = await db_session.scalar(
            text("SELECT has_table_privilege('openscientist_app', :t, 'UPDATE')"),
            {"t": table},
        )
        can_delete = await db_session.scalar(
            text("SELECT has_table_privilege('openscientist_app', :t, 'DELETE')"),
            {"t": table},
        )

        assert rls is True
        assert can_update is False
        assert can_delete is False
