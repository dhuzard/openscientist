from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from openscientist.integrations.dvc.workflow import (
    DVCWorkflowConflictError,
    DVCWorkflowCorruptError,
    DVCWorkflowStage,
    DVCWorkflowStore,
)


def test_workflow_persists_versioned_resumable_lifecycle(tmp_path):
    dataset_id = f"dvc-{uuid4()}"
    checkpoint_id = f"dvc-assess-{uuid4()}"
    approval_id = f"approval-{uuid4()}"
    execution_id = f"dvc-exec-{uuid4()}"
    post_checkpoint_id = f"dvc-assess-{uuid4()}"
    store = DVCWorkflowStore(tmp_path, job_id="job-1")

    acquired = store.record_dataset(dataset_id)
    assert acquired.version == 1
    assert acquired.current_stage == DVCWorkflowStage.ACQUIRED

    retried = DVCWorkflowStore(tmp_path, job_id="job-1").record_dataset(dataset_id)
    assert retried.version == 1
    assert len(retried.transitions) == 1

    pre_assessed = store.record_checkpoint(
        checkpoint_id,
        is_pre=True,
        context_sha256="a" * 64,
    )
    assert pre_assessed.context_sha256 == "a" * 64

    approved = store.record_approval(
        approval_id,
        checkpoint_id=checkpoint_id,
        dataset_id=dataset_id,
        actor="scientist@example.org",
    )
    assert approved.current_stage == DVCWorkflowStage.APPROVED
    assert [item.to_stage for item in approved.transitions[-2:]] == [
        DVCWorkflowStage.PENDING_APPROVAL,
        DVCWorkflowStage.APPROVED,
    ]

    analyzed = store.record_execution(
        execution_id,
        dataset_id=dataset_id,
        operation="summarize_time_bins",
    )
    assert analyzed.current_stage == DVCWorkflowStage.ANALYZED

    post_assessed = store.record_checkpoint(post_checkpoint_id, is_pre=False)
    reported = store.record_report("final_report.md")

    assert post_assessed.current_stage == DVCWorkflowStage.POST_ASSESSED
    assert reported.current_stage == DVCWorkflowStage.REPORTED
    assert reported.version == 7
    assert reported.datasets == [dataset_id]
    assert reported.checkpoints == [checkpoint_id, post_checkpoint_id]
    assert reported.approvals == [approval_id]
    assert reported.executions == [execution_id]
    assert reported.reports == ["final_report.md"]
    assert [item.version for item in reported.transitions] == list(range(1, 8))
    assert [item.previous_version for item in reported.transitions] == list(range(7))


def test_workflow_rejects_out_of_order_transition(tmp_path):
    store = DVCWorkflowStore(tmp_path)

    with pytest.raises(DVCWorkflowConflictError, match="initialized -> analyzed"):
        store.transition(DVCWorkflowStage.ANALYZED, "analysis_service")

    assert not store.state_file.exists()


def test_workflow_detects_idempotency_and_version_conflicts(tmp_path):
    store = DVCWorkflowStore(tmp_path)
    store.transition(
        DVCWorkflowStage.INITIALIZED,
        "test",
        idempotency_key="retry-1",
        details={"attempt": 1},
    )

    with pytest.raises(DVCWorkflowConflictError, match="already used"):
        store.transition(
            DVCWorkflowStage.INITIALIZED,
            "test",
            idempotency_key="retry-1",
            details={"attempt": 2},
        )
    with pytest.raises(DVCWorkflowConflictError, match="expected 0, found 1"):
        store.transition(
            DVCWorkflowStage.INITIALIZED,
            "test",
            expected_version=0,
        )


def test_workflow_corruption_fails_closed(tmp_path):
    state_path = tmp_path / "dvc_workflow.json"
    state_path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(DVCWorkflowCorruptError, match="state is invalid"):
        DVCWorkflowStore(tmp_path).load()


def test_workflow_detects_tampered_transition_history(tmp_path):
    store = DVCWorkflowStore(tmp_path)
    store.record_dataset(f"dvc-{uuid4()}")
    payload = json.loads(store.state_file.read_text(encoding="utf-8"))
    payload["transitions"][0]["details"]["dataset_id"] = f"dvc-{uuid4()}"
    store.state_file.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(DVCWorkflowCorruptError, match="state is invalid"):
        store.load()


def test_workflow_failure_retry_is_idempotent(tmp_path):
    store = DVCWorkflowStore(tmp_path)

    first = store.record_failure(
        "import_dataset",
        RuntimeError("temporary failure"),
        idempotency_key="failure-1",
        actor="dvc_gateway",
    )
    retried = store.record_failure(
        "import_dataset",
        RuntimeError("temporary failure"),
        idempotency_key="failure-1",
        actor="dvc_gateway",
    )

    assert first.version == retried.version == 1
    assert len(retried.failures) == 1
    assert len(DVCWorkflowStore(tmp_path).load().failures) == 1


def test_partial_v01_workflow_is_migrated_and_can_resume(tmp_path):
    dataset_id = f"dvc-{uuid4()}"
    checkpoint_id = f"dvc-assess-{uuid4()}"
    timestamp = datetime.now(timezone.utc).isoformat()
    (tmp_path / "dvc_workflow.json").write_text(
        json.dumps(
            {
                "schema_version": "openscientist-dvc-workflow-state/0.1",
                "job_id": "job-legacy",
                "current_stage": "acquired",
                "transitions": [
                    {
                        "transition_id": f"trans-{uuid4()}",
                        "from_stage": "initialized",
                        "to_stage": "acquired",
                        "actor": "dvc_gateway",
                        "timestamp": timestamp,
                        "idempotency_key": f"acquire-{dataset_id}",
                        "details": {"dataset_id": dataset_id},
                    }
                ],
                "datasets": [dataset_id],
                "checkpoints": [],
                "approvals": [],
                "executions": [],
                "updated_at": timestamp,
            }
        ),
        encoding="utf-8",
    )
    store = DVCWorkflowStore(tmp_path, job_id="job-legacy")

    migrated = store.load()
    resumed = store.record_checkpoint(
        checkpoint_id,
        is_pre=True,
        context_sha256="b" * 64,
    )

    assert migrated.schema_version == "openscientist-dvc-workflow-state/0.2"
    assert migrated.version == 1
    assert resumed.version == 2
    persisted = json.loads(store.state_file.read_text(encoding="utf-8"))
    assert persisted["schema_version"] == "openscientist-dvc-workflow-state/0.2"
