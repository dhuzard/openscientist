from __future__ import annotations

import json
import shutil
import sys
import types
from pathlib import Path
from typing import Any, cast

import pandas as pd
import pytest

from openscientist.integrations.dvc.credentials import DVCConnection
from openscientist.integrations.dvc.models import DVCImportRequest
from openscientist.integrations.dvc.service import (
    DVCAcquisitionError,
    DVCAcquisitionService,
)


class FakeProvider:
    def resolve(self, connection_id: str) -> DVCConnection:
        return DVCConnection(
            connection_id=connection_id,
            api_key="never-write-this-secret",
            base_url="https://example.invalid",
        )


def _request(**updates: Any) -> DVCImportRequest:
    arguments: dict[str, Any] = {
        "connection_id": "lab",
        "cage_ids": ["S81P-40332", "S81P-40333"],
        "metric_id": "EDGE",
        "start": "2025-12-05T00:00:00+00:00",
        "stop": "2025-12-08T00:00:00+00:00",
        "aggregation": "MINUTE",
    }
    arguments.update(updates)
    return DVCImportRequest(**arguments)


def _install_fake_ingest(monkeypatch) -> dict[str, int]:
    calls = {"count": 0}
    ingest = types.ModuleType("udwa.ingest")

    def fetch_api_bundle(**kwargs: Any):
        calls["count"] += 1
        workdir = Path(kwargs["workdir"])
        raw_path = workdir / "vendor-output.zip"
        raw_path.write_bytes(f"vendor-call-{calls['count']}".encode())
        measurements = pd.DataFrame(
            {
                "timestamp_utc": [
                    "2025-12-05T00:00:00Z",
                    "2025-12-05T00:01:00Z",
                ],
                "subject_id": [kwargs["cage_ids"][0], kwargs["cage_ids"][0]],
                "value": [1.0, 2.0],
            }
        )
        events = pd.DataFrame({"timestamp_utc": ["2025-12-05T00:00:30Z"], "event_type": ["X"]})
        return (
            measurements,
            events,
            [],
            {
                "taskId": calls["count"],
                "state": "COMPLETED",
                "outputPath": str(raw_path),
            },
        )

    cast(Any, ingest).fetch_api_bundle = fetch_api_bundle
    package = types.ModuleType("udwa")
    cast(Any, package).ingest = ingest
    monkeypatch.setitem(sys.modules, "udwa", package)
    monkeypatch.setitem(sys.modules, "udwa.ingest", ingest)
    return calls


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
        start="2025-12-05T00:00:00Z",
        stop="2025-12-08T00:00:00Z",
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
    assert len(manifest["request_fingerprint"]) == 64
    assert all(len(asset.sha256) == 64 for asset in result.assets)


def test_repeated_identical_import_reuses_verified_dataset(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls = _install_fake_ingest(monkeypatch)
    service = DVCAcquisitionService(tmp_path, FakeProvider())

    first = service.import_dataset(_request())
    repeated = service.import_dataset(
        _request(
            cage_ids=["S81P-40333", "S81P-40332"],
            start="2025-12-05T01:00:00+01:00",
            stop="2025-12-08T01:00:00+01:00",
        )
    )

    assert calls["count"] == 1
    assert first.reused is False
    assert repeated.reused is True
    assert repeated.dataset_id == first.dataset_id
    assert repeated.request_fingerprint == first.request_fingerprint
    assert {asset.role for asset in repeated.assets} == {
        "raw_export",
        "normalized_measurements",
        "normalized_events",
        "manifest",
    }


def test_changed_import_parameters_create_new_dataset(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls = _install_fake_ingest(monkeypatch)
    service = DVCAcquisitionService(tmp_path, FakeProvider())

    first = service.import_dataset(_request())
    changed = service.import_dataset(_request(stop="2025-12-09T00:00:00+00:00"))

    assert calls["count"] == 2
    assert changed.reused is False
    assert changed.dataset_id != first.dataset_id
    assert changed.request_fingerprint != first.request_fingerprint


def test_existing_report_job_legacy_manifest_can_be_reused(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls = _install_fake_ingest(monkeypatch)
    first = DVCAcquisitionService(tmp_path, FakeProvider()).import_dataset(_request())
    manifest_path = tmp_path / "dvc_datasets" / first.dataset_id / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema"] = "openscientist-dvc-dataset/0.1"
    manifest.pop("request_fingerprint")
    manifest["result"].pop("request_fingerprint")
    manifest["result"].pop("reused")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "final_report.md").write_text("# Existing report\n", encoding="utf-8")

    resumed = DVCAcquisitionService(tmp_path, FakeProvider()).import_dataset(_request())

    assert calls["count"] == 1
    assert resumed.reused is True
    assert resumed.dataset_id == first.dataset_id
    assert resumed.request_fingerprint == first.request_fingerprint


def test_existing_manifest_reuse_accepts_relative_job_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls = _install_fake_ingest(monkeypatch)
    absolute_job_dir = tmp_path / "job"
    first = DVCAcquisitionService(absolute_job_dir, FakeProvider()).import_dataset(_request())
    monkeypatch.chdir(tmp_path)

    resumed = DVCAcquisitionService(Path("job"), FakeProvider()).import_dataset(_request())

    assert calls["count"] == 1
    assert resumed.reused is True
    assert resumed.dataset_id == first.dataset_id
    manifest_asset = next(asset for asset in resumed.assets if asset.role == "manifest")
    assert manifest_asset.relative_path.endswith("manifest.json")


def test_corrupt_matching_import_blocks_without_vendor_refetch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls = _install_fake_ingest(monkeypatch)
    service = DVCAcquisitionService(tmp_path, FakeProvider())
    first = service.import_dataset(_request())
    measurements_path = tmp_path / "dvc_datasets" / first.dataset_id / "measurements.csv"
    measurements_path.write_text("tampered\n", encoding="utf-8")

    with pytest.raises(DVCAcquisitionError, match="integrity checks"):
        service.import_dataset(_request())

    assert calls["count"] == 1


def test_ambiguous_matching_imports_block_without_vendor_refetch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls = _install_fake_ingest(monkeypatch)
    service = DVCAcquisitionService(tmp_path, FakeProvider())
    first = service.import_dataset(_request())
    source = tmp_path / "dvc_datasets" / first.dataset_id
    duplicate = tmp_path / "dvc_datasets" / "dvc-duplicate"
    shutil.copytree(source, duplicate)

    with pytest.raises(DVCAcquisitionError, match="Multiple existing DVC imports"):
        service.import_dataset(_request())

    assert calls["count"] == 1


def test_vendor_state_is_allowlisted(tmp_path: Path) -> None:
    service = DVCAcquisitionService(tmp_path, FakeProvider())
    dataset_dir = tmp_path / "dvc_datasets" / "dvc-test"
    dataset_dir.mkdir(parents=True)
    request = DVCImportRequest(
        connection_id="lab",
        cage_ids=["S81P-40332"],
        metric_id="EDGE",
        start="2025-12-05T00:00:00Z",
        stop="2025-12-08T00:00:00Z",
    )

    result = service._persist(
        dataset_id="dvc-test",
        dataset_dir=dataset_dir,
        request=request,
        measurements=pd.DataFrame(),
        events=pd.DataFrame(),
        warnings=[],
        state={
            "taskId": 42,
            "state": "COMPLETED",
            "apiKey": "secret",
            "errorMessage": "api_key=secret",
        },
        sensitive_values=("secret",),
    )

    assert result.vendor_state == {
        "taskId": 42,
        "state": "COMPLETED",
        "errorMessage": "api_key=[REDACTED]",
    }


def test_connection_failure_redacts_bare_api_key(tmp_path: Path, monkeypatch) -> None:
    ingest = types.ModuleType("udwa.ingest")

    def fail_connection(*, base_url: str, api_key: str) -> bool:
        raise RuntimeError(f"vendor rejected credential {api_key} at {base_url}")

    cast(Any, ingest).test_api_connection = fail_connection
    package = types.ModuleType("udwa")
    cast(Any, package).ingest = ingest
    monkeypatch.setitem(sys.modules, "udwa", package)
    monkeypatch.setitem(sys.modules, "udwa.ingest", ingest)

    with pytest.raises(DVCAcquisitionError) as error:
        DVCAcquisitionService(tmp_path, FakeProvider()).test_connection("lab")

    assert "never-write-this-secret" not in str(error.value)
    assert "[REDACTED]" in str(error.value)
