from __future__ import annotations

import hashlib
import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pytest

from openscientist.integrations.dvc import execution
from openscientist.integrations.dvc.execution import (
    DVCAnalysisApproval,
    DVCAnalysisBlockedError,
    DVCAnalysisError,
    DVCAnalysisRequest,
    DVCAnalysisService,
    _assessment_conflict_blockers,
    canonical_checkpoint_sha256,
    canonical_context_sha256,
    canonical_parameters_sha256,
)
from openscientist.integrations.udwa import UdwaCompatibilityReport
from openscientist.preclinical_context.models import PreclinicalStudyContext


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_dataset(job_dir: Path) -> str:
    dataset_id = f"dvc-{uuid4()}"
    dataset_dir = job_dir / "dvc_datasets" / dataset_id
    dataset_dir.mkdir(parents=True)
    measurements = dataset_dir / "measurements.csv"
    pd.DataFrame(
        {
            "timestamp_utc": ["2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z"],
            "subject_id": ["CAGE-1", "CAGE-1"],
            "value": [1.0, 2.0],
        }
    ).to_csv(measurements, index=False)
    manifest = {
        "schema": "openscientist-dvc-dataset/0.1",
        "result": {
            "assets": [
                {
                    "role": "normalized_measurements",
                    "sha256": sha256(measurements),
                }
            ]
        },
    }
    (dataset_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return dataset_id


def install_fake_udwa(monkeypatch, calls: list[str] | None = None):
    orchestrator = types.ModuleType("udwa.orchestrator")

    def run_tool(name, dataframe, **parameters):
        if calls is not None:
            calls.append(name)
        assert name == "check_data_sanity"
        assert len(dataframe) == 2
        return {
            "tool": name,
            "specialist": "steward",
            "authorization": "analysis-secret",
            "columns": ["status"],
            "n_rows": 1,
            "records": [{"status": "PASS"}],
            "warnings": ["token=warning-secret"],
        }

    orchestrator.run_tool = run_tool
    package = types.ModuleType("udwa")
    package.orchestrator = orchestrator
    monkeypatch.setitem(sys.modules, "udwa", package)
    monkeypatch.setitem(sys.modules, "udwa.orchestrator", orchestrator)
    monkeypatch.setattr(
        execution,
        "inspect_udwa_compatibility",
        lambda: UdwaCompatibilityReport(
            distribution_version="0.1.0",
            pinned_commit="abc123",
            missing_imports=(),
            missing_operations=(),
        ),
    )


def make_pre_checkpoint(job_dir: Path, dataset_id: str, context: PreclinicalStudyContext) -> str:
    checkpoint_id = f"dvc-assess-{uuid4()}"
    assessment_dir = job_dir / "dvc_assessments"
    assessment_dir.mkdir()
    (assessment_dir / f"{checkpoint_id}.json").write_text(
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
    return checkpoint_id


def test_execution_writes_result_and_complete_provenance(tmp_path, monkeypatch):
    dataset_id = make_dataset(tmp_path)
    context = PreclinicalStudyContext(study_id="study-1")
    checkpoint_id = make_pre_checkpoint(tmp_path, dataset_id, context)
    install_fake_udwa(monkeypatch)
    monkeypatch.setenv("OPENSCIENTIST_COMMIT", "open-commit")

    result = DVCAnalysisService(tmp_path).execute(
        DVCAnalysisRequest(
            dataset_id=dataset_id,
            pre_analysis_checkpoint_id=checkpoint_id,
            operation="check_data_sanity",
            context=context,
        )
    )

    assert result.status == "completed"
    assert result.records == [{"status": "PASS"}]
    assert result.provenance["udwa_distribution_version"] == "0.1.0"
    assert result.provenance["udwa_pinned_commit"] == "abc123"
    assert result.provenance["openscientist_commit"] == "open-commit"
    assert result.provenance["operation_contract"]["contract_version"] == "1.0.0"
    assert len(result.provenance["operation_contract_sha256"]) == 64
    assert len(result.provenance["request_sha256"]) == 64
    assert result.provenance["approval"] is None
    assert {asset.role for asset in result.assets} == {"result", "provenance"}
    for asset in result.assets:
        path = tmp_path / asset.relative_path
        assert path.is_file()
        assert sha256(path) == asset.sha256
    serialized = json.dumps(result.model_dump(mode="json"))
    serialized += "".join(
        (tmp_path / asset.relative_path).read_text(encoding="utf-8") for asset in result.assets
    )
    assert "analysis-secret" not in serialized
    assert "warning-secret" not in serialized
    assert "[REDACTED]" in serialized


def test_completed_deterministic_execution_is_reused(tmp_path, monkeypatch):
    dataset_id = make_dataset(tmp_path)
    context = PreclinicalStudyContext(study_id="study-1")
    checkpoint_id = make_pre_checkpoint(tmp_path, dataset_id, context)
    calls: list[str] = []
    install_fake_udwa(monkeypatch, calls)
    request = DVCAnalysisRequest(
        dataset_id=dataset_id,
        pre_analysis_checkpoint_id=checkpoint_id,
        operation="check_data_sanity",
        context=context,
    )

    first = DVCAnalysisService(tmp_path).execute(request)
    resumed = DVCAnalysisService(tmp_path).execute(request)

    assert resumed.execution_id == first.execution_id
    assert resumed.provenance["request_sha256"] == first.provenance["request_sha256"]
    assert calls == ["check_data_sanity"]
    assert len(list((tmp_path / "dvc_analyses").iterdir())) == 1
    workflow = json.loads((tmp_path / "dvc_workflow.json").read_text(encoding="utf-8"))
    assert workflow["current_stage"] == "analyzed"
    assert workflow["executions"] == [first.execution_id]


def test_reuse_rechecks_dataset_integrity_before_returning_result(tmp_path, monkeypatch):
    dataset_id = make_dataset(tmp_path)
    context = PreclinicalStudyContext(study_id="study-1")
    checkpoint_id = make_pre_checkpoint(tmp_path, dataset_id, context)
    calls: list[str] = []
    install_fake_udwa(monkeypatch, calls)
    request = DVCAnalysisRequest(
        dataset_id=dataset_id,
        pre_analysis_checkpoint_id=checkpoint_id,
        operation="check_data_sanity",
        context=context,
    )
    DVCAnalysisService(tmp_path).execute(request)
    measurements = tmp_path / "dvc_datasets" / dataset_id / "measurements.csv"
    measurements.write_text(measurements.read_text() + "tampered", encoding="utf-8")

    with pytest.raises(DVCAnalysisError, match="integrity"):
        DVCAnalysisService(tmp_path).execute(request)

    assert calls == ["check_data_sanity"]


def test_matching_execution_with_corrupt_result_refuses_rerun(tmp_path, monkeypatch):
    dataset_id = make_dataset(tmp_path)
    context = PreclinicalStudyContext(study_id="study-1")
    checkpoint_id = make_pre_checkpoint(tmp_path, dataset_id, context)
    calls: list[str] = []
    install_fake_udwa(monkeypatch, calls)
    request = DVCAnalysisRequest(
        dataset_id=dataset_id,
        pre_analysis_checkpoint_id=checkpoint_id,
        operation="check_data_sanity",
        context=context,
    )
    first = DVCAnalysisService(tmp_path).execute(request)
    result_path = tmp_path / "dvc_analyses" / first.execution_id / "result.json"
    result_path.write_text("{invalid", encoding="utf-8")

    with pytest.raises(DVCAnalysisError, match="invalid result artifact"):
        DVCAnalysisService(tmp_path).execute(request)

    assert calls == ["check_data_sanity"]


def test_modified_measurements_are_rejected(tmp_path, monkeypatch):
    dataset_id = make_dataset(tmp_path)
    context = PreclinicalStudyContext(study_id="study-1")
    checkpoint_id = make_pre_checkpoint(tmp_path, dataset_id, context)
    install_fake_udwa(monkeypatch)
    measurements = tmp_path / "dvc_datasets" / dataset_id / "measurements.csv"
    measurements.write_text(measurements.read_text() + "tampered", encoding="utf-8")

    with pytest.raises(DVCAnalysisError, match="integrity"):
        DVCAnalysisService(tmp_path).execute(
            DVCAnalysisRequest(
                dataset_id=dataset_id,
                pre_analysis_checkpoint_id=checkpoint_id,
                operation="check_data_sanity",
                context=context,
            )
        )


def test_incompatible_udwa_fails_before_execution(tmp_path, monkeypatch):
    dataset_id = make_dataset(tmp_path)
    context = PreclinicalStudyContext(study_id="study-1")
    checkpoint_id = make_pre_checkpoint(tmp_path, dataset_id, context)
    monkeypatch.setattr(
        execution,
        "inspect_udwa_compatibility",
        lambda: UdwaCompatibilityReport(
            distribution_version="not-installed",
            pinned_commit="abc123",
            missing_imports=("udwa.orchestrator:run_tool",),
            missing_operations=(),
        ),
    )

    with pytest.raises(DVCAnalysisError, match="missing imports"):
        DVCAnalysisService(tmp_path).execute(
            DVCAnalysisRequest(
                dataset_id=dataset_id,
                pre_analysis_checkpoint_id=checkpoint_id,
                operation="check_data_sanity",
                context=context,
            )
        )


def test_analysis_without_pre_checkpoint_fails_closed(tmp_path):
    dataset_id = make_dataset(tmp_path)

    with pytest.raises(DVCAnalysisBlockedError, match="blocked") as exc:
        DVCAnalysisService(tmp_path).execute(
            DVCAnalysisRequest(
                dataset_id=dataset_id,
                pre_analysis_checkpoint_id=f"dvc-assess-{uuid4()}",
                operation="check_data_sanity",
                context=PreclinicalStudyContext(study_id="study-1"),
            )
        )

    assert exc.value.blockers == ["A matching pre-analysis assessment checkpoint is required."]


def test_analysis_rejects_checkpoint_for_different_context(tmp_path):
    dataset_id = make_dataset(tmp_path)
    assessed_context = PreclinicalStudyContext(study_id="study-assessed")
    checkpoint_id = make_pre_checkpoint(tmp_path, dataset_id, assessed_context)

    with pytest.raises(DVCAnalysisBlockedError) as exc:
        DVCAnalysisService(tmp_path).execute(
            DVCAnalysisRequest(
                dataset_id=dataset_id,
                pre_analysis_checkpoint_id=checkpoint_id,
                operation="check_data_sanity",
                context=PreclinicalStudyContext(study_id="study-changed"),
            )
        )

    assert any("study context" in blocker for blocker in exc.value.blockers)


def test_analysis_rejects_checkpoint_modified_after_approval(tmp_path):
    dataset_id = make_dataset(tmp_path)
    context = PreclinicalStudyContext(study_id="study-1")
    checkpoint_id = make_pre_checkpoint(tmp_path, dataset_id, context)
    checkpoint_path = tmp_path / "dvc_assessments" / f"{checkpoint_id}.json"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    approval = DVCAnalysisApproval(
        approval_id="approval-1",
        approved_by="scientist@example.org",
        approved_at=datetime.now(timezone.utc),
        dataset_id=dataset_id,
        pre_analysis_checkpoint_id=checkpoint_id,
        pre_analysis_checkpoint_sha256=canonical_checkpoint_sha256(checkpoint),
        operation="check_data_sanity",
        context_sha256=canonical_context_sha256(context),
        parameters_sha256=canonical_parameters_sha256({}),
    )
    checkpoint["assessments"] = [{"tampered": True}]
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")

    with pytest.raises(DVCAnalysisBlockedError) as exc:
        DVCAnalysisService(tmp_path).execute(
            DVCAnalysisRequest(
                dataset_id=dataset_id,
                pre_analysis_checkpoint_id=checkpoint_id,
                operation="check_data_sanity",
                context=context,
                approval=approval,
            )
        )

    assert any("approved checkpoint content" in blocker for blocker in exc.value.blockers)


def test_conflicting_light_cycle_assessment_blocks_biological_time_operation():
    checkpoint = {
        "assessments": [
            {
                "framework": "prepare-v1",
                "findings": [
                    {
                        "requirement_id": "environment.light_schedule",
                        "status": "conflicting",
                        "blocks": ["summarize_circadian_cosinor"],
                    }
                ],
            }
        ]
    }

    assert _assessment_conflict_blockers(
        checkpoint,
        "summarize_circadian_cosinor",
    ) == [
        "Conflicting assessment finding blocks biological-time analysis: environment.light_schedule"
    ]
    assert _assessment_conflict_blockers(checkpoint, "check_data_sanity") == []
