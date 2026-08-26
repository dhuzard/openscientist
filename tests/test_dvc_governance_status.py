from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from openscientist.dvc.governance_status import derive_dvc_governance_status
from openscientist.integrations.dvc.execution import (
    DVCAnalysisApproval,
    canonical_checkpoint_sha256,
    canonical_parameters_sha256,
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_dataset(job_dir: Path) -> str:
    dataset_id = f"dvc-{uuid4()}"
    dataset_dir = job_dir / "dvc_datasets" / dataset_id
    dataset_dir.mkdir(parents=True)
    measurements = dataset_dir / "measurements.csv"
    measurements.write_text(
        "timestamp_utc,subject_id,value\n2026-01-01T00:00:00Z,CAGE-1,1.0\n",
        encoding="utf-8",
    )
    _write_json(
        dataset_dir / "manifest.json",
        {
            "schema": "openscientist-dvc-dataset/0.1",
            "result": {
                "assets": [
                    {
                        "role": "normalized_measurements",
                        "sha256": _sha256(measurements),
                    }
                ]
            },
        },
    )
    return dataset_id


def _tool_call(call_id: str, tool: str, arguments: dict) -> dict:
    return {
        "type": "tool_call",
        "id": call_id,
        "tool": tool,
        "arguments": arguments,
    }


def _tool_result(call_id: str, structured_content: dict) -> dict:
    return {
        "type": "tool_result",
        "call_id": call_id,
        "success": True,
        "status": "completed",
        "structured_content": structured_content,
    }


def _make_analysis(
    job_dir: Path,
    dataset_id: str,
    *,
    operation: str,
    with_approval: bool,
    with_audit: bool = True,
) -> str:
    execution_id = f"dvc-exec-{uuid4()}"
    checkpoint_id = f"dvc-assess-{uuid4()}"
    context_hash = "a" * 64
    parameters = {"bin_minutes": 60}
    checkpoint = {
        "checkpoint_id": checkpoint_id,
        "checkpoint": "pre_analysis",
        "dataset_id": dataset_id,
        "context_sha256": context_hash,
        "created_at": "2026-01-01T09:00:00+00:00",
        "assessments": [{"framework": "prepare-v1"}],
    }
    _write_json(job_dir / "dvc_assessments" / f"{checkpoint_id}.json", checkpoint)

    approval_payload = None
    if with_approval:
        approval = DVCAnalysisApproval(
            approval_id=f"approval-{uuid4()}",
            approved_by="scientist@example.org",
            approved_at=datetime(2026, 1, 1, 10, tzinfo=timezone.utc),
            dataset_id=dataset_id,
            pre_analysis_checkpoint_id=checkpoint_id,
            pre_analysis_checkpoint_sha256=canonical_checkpoint_sha256(checkpoint),
            operation=operation,
            context_sha256=context_hash,
            parameters_sha256=canonical_parameters_sha256(parameters),
        )
        approval_payload = approval.model_dump(mode="json")
        approval_path = job_dir / "dvc_approvals" / f"{approval.approval_id}.json"
        _write_json(approval_path, approval_payload)
        if with_audit:
            _write_json(
                approval_path.with_name(f"{approval.approval_id}.audit.json"),
                {
                    "schema": "openscientist-dvc-approval-audit/0.1",
                    "approval_id": approval.approval_id,
                    "dataset_id": dataset_id,
                    "pre_analysis_checkpoint_id": checkpoint_id,
                    "created_via": "authenticated_rest_api",
                },
            )

    dataset_dir = job_dir / "dvc_datasets" / dataset_id
    execution_dir = job_dir / "dvc_analyses" / execution_id
    result = {
        "schema": "openscientist-dvc-analysis-result/0.1",
        "execution_id": execution_id,
        "dataset_id": dataset_id,
        "operation": operation,
        "status": "completed",
        "output": {"records": []},
    }
    provenance = {
        "schema": "openscientist-dvc-analysis-provenance/0.1",
        "execution_id": execution_id,
        "dataset_id": dataset_id,
        "dataset_manifest_sha256": _sha256(dataset_dir / "manifest.json"),
        "measurements_sha256": _sha256(dataset_dir / "measurements.csv"),
        "operation": operation,
        "parameters": parameters,
        "context_sha256": context_hash,
        "pre_analysis_checkpoint_id": checkpoint_id,
        "approval": approval_payload,
        "started_at": "2026-01-01T11:00:00+00:00",
        "completed_at": "2026-01-01T11:01:00+00:00",
    }
    _write_json(execution_dir / "result.json", result)
    _write_json(execution_dir / "provenance.json", provenance)
    return execution_id


def test_report_prose_is_never_governance_evidence(tmp_path: Path) -> None:
    (tmp_path / "final_report.md").write_text(
        "The fully approved governed DVC analysis completed.",
        encoding="utf-8",
    )

    assert derive_dvc_governance_status(tmp_path) is None


def test_structured_events_distinguish_diagnostics_exploration_and_block(
    tmp_path: Path,
) -> None:
    dataset_id = _make_dataset(tmp_path)
    transcript = [
        _tool_call(
            "diagnostic",
            "execute_code",
            {
                "description": "DVC data-contract sanity and missing-bin validation",
                "code": "print('coverage and duplicate checks')",
            },
        ),
        _tool_result("diagnostic", {"ok": True}),
        _tool_call(
            "exploratory",
            "execute_code",
            {
                "description": "Circadian association modelling",
                "code": "from scipy.stats import pearsonr\npearsonr(day1, day2)",
            },
        ),
        _tool_result("exploratory", {"ok": True}),
        _tool_call(
            "assessment",
            "dvc_assess_pre_analysis",
            {"dataset_id": dataset_id, "context": {}},
        ),
        _tool_result(
            "assessment",
            {
                "ok": False,
                "error": "FAIR-VCG hostname could not be resolved",
                "error_type": "FairPrepareError",
            },
        ),
    ]
    _write_json(tmp_path / "provenance" / "iter1_transcript.json", transcript)

    status = derive_dvc_governance_status(tmp_path)

    assert status is not None
    assert status.primary_state == "blocked"
    assert len(status.validation_diagnostics) == 1
    assert len(status.exploratory_computations) == 1
    assert len(status.governance_blocks) == 1
    assert not status.approved_governed_analyses
    assert status.governance_blocks[0].source == "provenance/iter1_transcript.json"
    assert status.to_dict()["schema"] == "openscientist-dvc-governance-status/0.1"


def test_complete_authenticated_chain_is_approved_governed_analysis(tmp_path: Path) -> None:
    dataset_id = _make_dataset(tmp_path)
    execution_id = _make_analysis(
        tmp_path,
        dataset_id,
        operation="summarize_time_bins",
        with_approval=True,
    )

    status = derive_dvc_governance_status(tmp_path)

    assert status is not None
    assert status.primary_state == "approved"
    assert [item.identifier for item in status.approved_governed_analyses] == [execution_id]
    assert not status.unverified_analyses


def test_missing_authenticated_audit_fails_closed(tmp_path: Path) -> None:
    dataset_id = _make_dataset(tmp_path)
    _make_analysis(
        tmp_path,
        dataset_id,
        operation="summarize_time_bins",
        with_approval=True,
        with_audit=False,
    )

    status = derive_dvc_governance_status(tmp_path)

    assert status is not None
    assert status.primary_state == "blocked"
    assert not status.approved_governed_analyses
    assert len(status.unverified_analyses) == 1
    assert "authenticated approval audit" in (status.unverified_analyses[0].detail or "")


def test_allowlisted_no_approval_operation_is_validation_diagnostic(tmp_path: Path) -> None:
    dataset_id = _make_dataset(tmp_path)
    execution_id = _make_analysis(
        tmp_path,
        dataset_id,
        operation="check_data_sanity",
        with_approval=False,
    )

    status = derive_dvc_governance_status(tmp_path)

    assert status is not None
    assert status.primary_state == "diagnostic"
    assert [item.identifier for item in status.validation_diagnostics] == [execution_id]
    assert not status.approved_governed_analyses


def test_analysis_provenance_rejects_invalid_dataset_path(tmp_path: Path) -> None:
    execution_id = f"dvc-exec-{uuid4()}"
    execution_dir = tmp_path / "dvc_analyses" / execution_id
    _write_json(
        execution_dir / "result.json",
        {
            "schema": "openscientist-dvc-analysis-result/0.1",
            "execution_id": execution_id,
            "dataset_id": "../../outside",
            "operation": "check_data_sanity",
            "status": "completed",
        },
    )
    _write_json(
        execution_dir / "provenance.json",
        {
            "schema": "openscientist-dvc-analysis-provenance/0.1",
            "execution_id": execution_id,
            "dataset_id": "../../outside",
            "operation": "check_data_sanity",
        },
    )

    status = derive_dvc_governance_status(tmp_path)

    assert status is not None
    assert status.primary_state == "blocked"
    assert "dataset id is missing or invalid" in (status.unverified_analyses[0].detail or "")
