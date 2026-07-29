from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from openscientist.integrations.dvc.credentials import DVCConnection
from openscientist.integrations.dvc.models import DVCImportRequest
from openscientist.integrations.dvc.service import DVCAcquisitionService


class FakeProvider:
    def resolve(self, connection_id: str) -> DVCConnection:
        return DVCConnection(
            connection_id=connection_id,
            api_key="never-write-this-secret",
            base_url="https://example.invalid",
        )


def test_persist_creates_hashed_job_assets_without_secret(tmp_path: Path) -> None:
    service = DVCAcquisitionService(tmp_path, FakeProvider())
    dataset_id = "dvc-test"
    dataset_dir = tmp_path / "dvc_datasets" / dataset_id
    dataset_dir.mkdir(parents=True)
    raw_path = dataset_dir / "vendor.zip"
    raw_path.write_bytes(b"fake zip")

    request = DVCImportRequest(
        connection_id="lab",
        cage_ids=["S81P-40332"],
        metric_id="EDGE",
        start="2025-12-05",
        stop="2025-12-08",
        aggregation="MINUTE",
    )
    measurements = pd.DataFrame(
        {
            "timestamp_utc": ["2025-12-05T00:00:00Z", "2025-12-05T00:01:00Z"],
            "subject_id": ["S81P-40332", "S81P-40332"],
            "value": [1.0, 2.0],
        }
    )
    events = pd.DataFrame({"timestamp_utc": ["2025-12-05T00:00:30Z"], "event_type": ["X"]})

    result = service._persist(
        dataset_id=dataset_id,
        dataset_dir=dataset_dir,
        request=request,
        measurements=measurements,
        events=events,
        warnings=["fixture warning"],
        state={"taskId": 42, "state": "COMPLETED", "outputPath": str(raw_path)},
    )

    assert result.inspection.row_count == 2
    assert result.inspection.event_count == 1
    assert {asset.role for asset in result.assets} == {
        "raw_export",
        "normalized_measurements",
        "normalized_events",
        "manifest",
    }
    manifest = json.loads((dataset_dir / "manifest.json").read_text(encoding="utf-8"))
    serialized = json.dumps(manifest)
    assert "never-write-this-secret" not in serialized
    assert manifest["schema"] == "openscientist-dvc-dataset/0.1"
    assert all(len(asset.sha256) == 64 for asset in result.assets)


def test_vendor_state_is_allowlisted(tmp_path: Path) -> None:
    service = DVCAcquisitionService(tmp_path, FakeProvider())
    dataset_dir = tmp_path / "dvc_datasets" / "dvc-test"
    dataset_dir.mkdir(parents=True)
    request = DVCImportRequest(
        connection_id="lab",
        cage_ids=["S81P-40332"],
        metric_id="EDGE",
        start="2025-12-05",
        stop="2025-12-08",
    )

    result = service._persist(
        dataset_id="dvc-test",
        dataset_dir=dataset_dir,
        request=request,
        measurements=pd.DataFrame(),
        events=pd.DataFrame(),
        warnings=[],
        state={"taskId": 42, "state": "COMPLETED", "apiKey": "secret"},
    )

    assert result.vendor_state == {"taskId": 42, "state": "COMPLETED"}
