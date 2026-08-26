from __future__ import annotations

from pathlib import Path

from openscientist.assays import AnalysisRunStage, AnalysisRunStore
from openscientist.assays.review import (
    approve_generic_run,
    list_assay_reviews,
)
from openscientist.integrations.open_field.adapter import OPEN_FIELD_ADAPTER


def test_generic_review_discovers_and_approves_exact_pending_run(tmp_path: Path) -> None:
    store = AnalysisRunStore.for_analysis(
        tmp_path,
        study_id="study-1",
        assay_id="open-field",
        dataset_id="open-field-" + "a" * 24,
        operation_id="summarize_distance",
        context_sha256="b" * 64,
        parameters_sha256="c" * 64,
    )
    store.record_dataset(store.dataset_id)
    store.record_checkpoint("checkpoint-1", is_pre=True, context_sha256="b" * 64)
    store.transition(
        AnalysisRunStage.PENDING_APPROVAL,
        "open_field_service",
        idempotency_key="request-approval",
    )

    reviews = [
        item for item in list_assay_reviews(tmp_path) if item.adapter.adapter_id == "open-field"
    ]
    assert len(reviews) == 1
    assert reviews[0].checkpoint["run_id"] == store.run_id
    assert not reviews[0].checkpoint["approved"]

    decision = approve_generic_run(
        job_dir=tmp_path,
        adapter=OPEN_FIELD_ADAPTER,
        checkpoint=reviews[0].checkpoint,
        decided_by="scientist@example.org",
        rationale="Metadata and inferential unit reviewed.",
    )

    state = store.load()
    assert state.current_stage is AnalysisRunStage.APPROVED
    assert state.approval_decisions == [decision]
    assert decision.context_sha256 == "b" * 64
    assert decision.parameters_sha256 == "c" * 64
