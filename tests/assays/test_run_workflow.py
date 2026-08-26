from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from openscientist.assays import (
    AnalysisRunConflictError,
    AnalysisRunCorruptError,
    AnalysisRunStage,
    AnalysisRunStore,
    ApprovalDecision,
    EvidenceArtifact,
)


def store_for(tmp_path, *, operation: str, parameters_sha256: str) -> AnalysisRunStore:
    return AnalysisRunStore.for_analysis(
        tmp_path,
        study_id="study-1",
        assay_id="dvc",
        dataset_id="dvc-00000000-0000-0000-0000-000000000000",
        operation_id=operation,
        context_sha256="a" * 64,
        parameters_sha256=parameters_sha256,
        job_id="job-1",
    )


def complete_to_analysis(store: AnalysisRunStore, execution_id: str) -> None:
    store.record_dataset(store.dataset_id)
    store.record_checkpoint("checkpoint-1", is_pre=True, context_sha256="a" * 64)
    store.record_execution(
        execution_id,
        dataset_id=store.dataset_id,
        operation=store.operation_id,
    )


def test_runs_are_scoped_by_dataset_analysis_identity(tmp_path):
    first = store_for(tmp_path, operation="check_data_sanity", parameters_sha256="b" * 64)
    second = store_for(tmp_path, operation="summarize_time_bins", parameters_sha256="b" * 64)
    parameter_variant = store_for(
        tmp_path,
        operation="summarize_time_bins",
        parameters_sha256="c" * 64,
    )

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [
            executor.submit(complete_to_analysis, first, "execution-1"),
            executor.submit(complete_to_analysis, second, "execution-2"),
            executor.submit(complete_to_analysis, parameter_variant, "execution-3"),
        ]
        for future in futures:
            future.result()

    assert len({first.run_id, second.run_id, parameter_variant.run_id}) == 3
    assert first.state_file != second.state_file != parameter_variant.state_file
    assert first.load().executions == ["execution-1"]
    assert second.load().executions == ["execution-2"]
    assert parameter_variant.load().executions == ["execution-3"]


def test_transition_history_is_hash_chained_and_tampering_fails_closed(tmp_path):
    store = store_for(tmp_path, operation="check_data_sanity", parameters_sha256="b" * 64)
    complete_to_analysis(store, "execution-1")
    state = store.load()

    assert state.transitions[0].previous_transition_sha256 is None
    assert state.transitions[1].previous_transition_sha256 == state.transitions[0].transition_sha256

    payload = json.loads(store.state_file.read_text(encoding="utf-8"))
    payload["transitions"][0]["details"]["dataset_id"] = "tampered"
    store.state_file.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(AnalysisRunCorruptError, match="state is invalid"):
        store.load()


def test_retry_is_idempotent_and_conflicting_payload_is_rejected(tmp_path):
    store = store_for(tmp_path, operation="check_data_sanity", parameters_sha256="b" * 64)
    first = store.transition(
        AnalysisRunStage.INITIALIZED,
        "test",
        idempotency_key="retry",
        details={"attempt": 1},
    )
    retried = store.transition(
        AnalysisRunStage.INITIALIZED,
        "test",
        idempotency_key="retry",
        details={"attempt": 1},
    )

    assert first.version == retried.version == 1
    with pytest.raises(AnalysisRunConflictError, match="already used"):
        store.transition(
            AnalysisRunStage.INITIALIZED,
            "test",
            idempotency_key="retry",
            details={"attempt": 2},
        )


def test_evidence_is_content_addressed_and_cannot_change_in_place(tmp_path):
    store = store_for(tmp_path, operation="check_data_sanity", parameters_sha256="b" * 64)
    store.record_evidence(
        EvidenceArtifact(
            artifact_id="artifact-1",
            run_id=store.run_id,
            assay_id="dvc",
            dataset_id=store.dataset_id,
            role="manifest",
            relative_path="outputs/manifest.json",
            sha256="d" * 64,
            bytes=10,
        )
    )

    with pytest.raises(AnalysisRunConflictError, match="changed after registration"):
        store.record_evidence(
            EvidenceArtifact(
                artifact_id="artifact-1",
                run_id=store.run_id,
                assay_id="dvc",
                dataset_id=store.dataset_id,
                role="manifest",
                relative_path="outputs/manifest.json",
                sha256="e" * 64,
                bytes=10,
            )
        )


def test_typed_approval_decision_is_persisted_on_the_exact_run(tmp_path):
    store = store_for(tmp_path, operation="summarize_time_bins", parameters_sha256="b" * 64)
    store.record_dataset(store.dataset_id)
    store.record_checkpoint("checkpoint-1", is_pre=True, context_sha256="a" * 64)
    decision = ApprovalDecision(
        approval_id="approval-1",
        run_id=store.run_id,
        assay_id="dvc",
        dataset_id=store.dataset_id,
        operation_id=store.operation_id,
        contract_sha256="c" * 64,
        context_sha256="a" * 64,
        parameters_sha256="b" * 64,
        decided_by="scientist@example.org",
        decided_at=datetime.now(timezone.utc),
        decision="approved",
    )

    store.record_approval(
        decision.approval_id,
        checkpoint_id="checkpoint-1",
        dataset_id=store.dataset_id,
        actor=decision.decided_by,
        decision=decision,
    )

    persisted = store.load()
    assert persisted.current_stage == AnalysisRunStage.APPROVED
    assert persisted.approval_decisions == [decision]
