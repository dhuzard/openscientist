from __future__ import annotations

import json
from datetime import datetime, timezone

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
