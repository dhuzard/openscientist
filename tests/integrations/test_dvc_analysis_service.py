from __future__ import annotations

import hashlib
import json
import sys
import types
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pytest

from openscientist.integrations.dvc import execution
from openscientist.integrations.dvc.execution import (
    DVCAnalysisError,
    DVCAnalysisRequest,
    DVCAnalysisService,
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


def install_fake_udwa(monkeypatch):
    orchestrator = types.ModuleType("udwa.orchestrator")

    def run_tool(name, dataframe, **parameters):
        assert name == "check_data_sanity"
        assert len(dataframe) == 2
        return {
            "tool": name,
            "specialist": "steward",
            "columns": ["status"],
            "n_rows": 1,
            "records": [{"status": "PASS"}],
            "warnings": ["fixture warning"],
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


def test_execution_writes_result_and_complete_provenance(tmp_path, monkeypatch):
    dataset_id = make_dataset(tmp_path)
    install_fake_udwa(monkeypatch)
    monkeypatch.setenv("OPENSCIENTIST_COMMIT", "open-commit")

    result = DVCAnalysisService(tmp_path).execute(
        DVCAnalysisRequest(
            dataset_id=dataset_id,
            operation="check_data_sanity",
            context=PreclinicalStudyContext(study_id="study-1"),
        )
    )

    assert result.status == "completed"
    assert result.records == [{"status": "PASS"}]
    assert result.provenance["udwa_distribution_version"] == "0.1.0"
    assert result.provenance["udwa_pinned_commit"] == "abc123"
    assert result.provenance["openscientist_commit"] == "open-commit"
    assert result.provenance["approval"] is None
    assert {asset.role for asset in result.assets} == {"result", "provenance"}
    for asset in result.assets:
        path = tmp_path / asset.relative_path
        assert path.is_file()
        assert sha256(path) == asset.sha256


def test_modified_measurements_are_rejected(tmp_path, monkeypatch):
    dataset_id = make_dataset(tmp_path)
    install_fake_udwa(monkeypatch)
    measurements = tmp_path / "dvc_datasets" / dataset_id / "measurements.csv"
    measurements.write_text(measurements.read_text() + "tampered", encoding="utf-8")

    with pytest.raises(DVCAnalysisError, match="integrity"):
        DVCAnalysisService(tmp_path).execute(
            DVCAnalysisRequest(
                dataset_id=dataset_id,
                operation="check_data_sanity",
                context=PreclinicalStudyContext(study_id="study-1"),
            )
        )


def test_incompatible_udwa_fails_before_execution(tmp_path, monkeypatch):
    dataset_id = make_dataset(tmp_path)
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
                operation="check_data_sanity",
                context=PreclinicalStudyContext(study_id="study-1"),
            )
        )
