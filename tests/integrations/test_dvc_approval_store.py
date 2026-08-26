from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from openscientist.integrations.dvc.approvals import (
    DVCApprovalNotFoundError,
    FileDVCApprovalStore,
)


def test_approval_store_resolves_valid_record(tmp_path):
    root = tmp_path / "dvc_approvals"
    root.mkdir()
    payload = {
        "approval_id": "approval-1",
        "approved_by": "scientist@example.org",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "dataset_id": "dvc-00000000-0000-0000-0000-000000000000",
        "pre_analysis_checkpoint_id": "dvc-assess-00000000-0000-0000-0000-000000000000",
        "pre_analysis_checkpoint_sha256": "c" * 64,
        "operation": "summarize_light_dark",
        "context_sha256": "a" * 64,
        "parameters_sha256": "b" * 64,
        "decision": "approved",
    }
    (root / "approval-1.json").write_text(json.dumps(payload), encoding="utf-8")

    approval = FileDVCApprovalStore(tmp_path).resolve("approval-1")

    assert approval.approval_id == "approval-1"
    assert approval.operation == "summarize_light_dark"


def test_missing_approval_fails_closed(tmp_path):
    with pytest.raises(DVCApprovalNotFoundError, match="not found"):
        FileDVCApprovalStore(tmp_path).resolve("approval-1")


def test_path_traversal_approval_id_is_rejected(tmp_path):
    with pytest.raises(DVCApprovalNotFoundError, match="Invalid"):
        FileDVCApprovalStore(tmp_path).resolve("../approval-1")


def test_record_id_must_match_filename(tmp_path):
    root = tmp_path / "dvc_approvals"
    root.mkdir()
    payload = {
        "approval_id": "different-id",
        "approved_by": "scientist@example.org",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "dataset_id": "dvc-00000000-0000-0000-0000-000000000000",
        "pre_analysis_checkpoint_id": "dvc-assess-00000000-0000-0000-0000-000000000000",
        "pre_analysis_checkpoint_sha256": "c" * 64,
        "operation": "summarize_light_dark",
        "context_sha256": "a" * 64,
        "parameters_sha256": "b" * 64,
        "decision": "approved",
    }
    (root / "approval-1.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(DVCApprovalNotFoundError, match="does not match"):
        FileDVCApprovalStore(tmp_path).resolve("approval-1")


def test_create_dvc_approval_record_and_list(tmp_path):
    from openscientist.integrations.dvc.approvals import (
        create_dvc_approval_record,
        list_dvc_pre_analysis_checkpoints,
        load_checkpoint_context,
    )
    from openscientist.integrations.dvc.execution import canonical_context_sha256
    from openscientist.preclinical_context.models import PreclinicalStudyContext

    context = PreclinicalStudyContext(study_id="study-test-1")
    context_sha256 = canonical_context_sha256(context)

    # Setup pre-analysis checkpoint
    assessments_dir = tmp_path / "dvc_assessments"
    assessments_dir.mkdir(parents=True)
    checkpoint_id = "dvc-assess-11111111-1111-1111-1111-111111111111"
    dataset_id = "dvc-00000000-0000-0000-0000-000000000000"
    checkpoint_data = {
        "checkpoint_id": checkpoint_id,
        "checkpoint": "pre_analysis",
        "dataset_id": dataset_id,
        "context_sha256": context_sha256,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "assessments": [{"framework": "prepare-v1", "satisfied_count": 5, "missing_count": 1}],
    }
    (assessments_dir / f"{checkpoint_id}.json").write_text(
        json.dumps(checkpoint_data), encoding="utf-8"
    )
    (assessments_dir / f"{checkpoint_id}.context.json").write_text(
        json.dumps(context.model_dump(mode="json")), encoding="utf-8"
    )

    # Initial check: checkpoint listed as unapproved
    checkpoints = list_dvc_pre_analysis_checkpoints(tmp_path)
    assert len(checkpoints) == 1
    assert checkpoints[0]["approved"] is False
    assert checkpoints[0]["checkpoint_id"] == checkpoint_id

    # Create approval record
    approval = create_dvc_approval_record(
        job_dir=tmp_path,
        dataset_id=dataset_id,
        pre_analysis_checkpoint_id=checkpoint_id,
        operation="summarize_time_bins",
        context=context,
        parameters={"aggregation": "HOUR"},
        approved_by="user@example.org",
        created_via="web_ui",
    )

    assert approval.approved_by == "user@example.org"
    assert approval.operation == "summarize_time_bins"

    # Verify store list
    store = FileDVCApprovalStore(tmp_path)
    stored_approvals = store.list_approvals()
    assert len(stored_approvals) == 1
    assert stored_approvals[0].approval_id == approval.approval_id

    # Verify checkpoint is now marked approved
    checkpoints_after = list_dvc_pre_analysis_checkpoints(tmp_path)
    assert len(checkpoints_after) == 1
    assert checkpoints_after[0]["approved"] is True
    assert checkpoints_after[0]["approval_id"] == approval.approval_id

    # Verify context loading
    loaded_ctx = load_checkpoint_context(tmp_path, checkpoint_id)
    assert loaded_ctx is not None
    assert loaded_ctx.study_id == "study-test-1"


def test_identical_approval_retry_reuses_record(tmp_path):
    from openscientist.integrations.dvc.approvals import create_dvc_approval_record
    from openscientist.integrations.dvc.execution import canonical_context_sha256
    from openscientist.preclinical_context.models import PreclinicalStudyContext

    context = PreclinicalStudyContext(study_id="study-test-retry")
    checkpoint_id = f"dvc-assess-{uuid4()}"
    dataset_id = f"dvc-{uuid4()}"
    assessments_dir = tmp_path / "dvc_assessments"
    assessments_dir.mkdir(parents=True)
    (assessments_dir / f"{checkpoint_id}.json").write_text(
        json.dumps(
            {
                "checkpoint_id": checkpoint_id,
                "checkpoint": "pre_analysis",
                "dataset_id": dataset_id,
                "context_sha256": canonical_context_sha256(context),
                "assessments": [],
            }
        ),
        encoding="utf-8",
    )

    first = create_dvc_approval_record(
        tmp_path,
        dataset_id,
        checkpoint_id,
        "summarize_time_bins",
        context,
        {"aggregation": "HOUR"},
        approved_by="scientist@example.org",
    )
    retried = create_dvc_approval_record(
        tmp_path,
        dataset_id,
        checkpoint_id,
        "summarize_time_bins",
        context,
        {"aggregation": "HOUR"},
        approved_by="scientist@example.org",
    )

    assert retried.approval_id == first.approval_id
    assert len(FileDVCApprovalStore(tmp_path).list_approvals()) == 1
