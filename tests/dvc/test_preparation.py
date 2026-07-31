from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from openscientist.dvc.ingestion import DVCIngestionError
from openscientist.dvc.models import DVCImportSpec, DVCSourceSpec
from openscientist.dvc.preparation import prepare_uploaded_dvc


def _type2(path: Path) -> None:
    pd.DataFrame(
        {
            "day": [1, 1],
            "hour": [0, 0],
            "minute": [0, 5],
            "relativeTime": [0, 300],
            "COLLAB_TIMESTAMP": [
                "2026-01-01T00:00:00+01:00",
                "2026-01-01T00:05:00+01:00",
            ],
            "COLLAB_CAGE_A": [1.0, 2.0],
            "COLLAB_CAGE_B": [3.0, 4.0],
            "COLLAB_AVG": [2.0, 3.0],
            "COLLAB_SEM": [1.0, 1.0],
            "COLLAB_SAMPLES": [2, 2],
        }
    ).to_csv(path, index=False)


def test_preparation_is_content_addressed_reconciled_and_reused(tmp_path: Path) -> None:
    source = tmp_path / "activity.csv"
    _type2(source)
    spec = DVCImportSpec(
        sources=[DVCSourceSpec(path=source, source_id="site-a", expected_trace_count=2)]
    )

    created = prepare_uploaded_dvc(tmp_path, spec)
    reused = prepare_uploaded_dvc(tmp_path, spec)

    assert created.dataset_id == reused.dataset_id
    assert created.reused is False
    assert reused.reused is True
    assert created.trace_count == 2
    dataset = tmp_path / "dvc_datasets" / created.dataset_id
    measurements = pd.read_parquet(dataset / "measurements.parquet")
    assert set(measurements["cage_id"]) == {"CAGE_A", "CAGE_B"}
    assert "AVG" not in set(measurements["cage_id"])
    manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["measurement_asset_id"] == created.measurement_asset_id
    assert manifest["artifact_sha256"]["measurements.parquet"]


def test_preparation_fails_closed_on_source_count_mismatch(tmp_path: Path) -> None:
    source = tmp_path / "activity.csv"
    _type2(source)
    spec = DVCImportSpec(
        sources=[DVCSourceSpec(path=source, source_id="site-a", expected_trace_count=3)]
    )

    with pytest.raises(DVCIngestionError, match="expected 3 physical traces, loaded 2"):
        prepare_uploaded_dvc(tmp_path, spec)


def test_preparation_requires_timezone_for_naive_source_time(tmp_path: Path) -> None:
    source = tmp_path / "activity.csv"
    _type2(source)
    frame = pd.read_csv(source)
    frame["COLLAB_TIMESTAMP"] = ["2026-01-01 00:00:00", "2026-01-01 00:05:00"]
    frame.to_csv(source, index=False)
    spec = DVCImportSpec(sources=[DVCSourceSpec(path=source, source_id="site-a")])

    with pytest.raises(DVCIngestionError, match="IANA timezone"):
        prepare_uploaded_dvc(tmp_path, spec)


def test_preparation_localizes_naive_time_from_source_spec(tmp_path: Path) -> None:
    source = tmp_path / "activity.csv"
    _type2(source)
    frame = pd.read_csv(source)
    frame["COLLAB_TIMESTAMP"] = ["2026-01-01 00:00:00", "2026-01-01 00:05:00"]
    frame.to_csv(source, index=False)
    spec = DVCImportSpec(
        sources=[DVCSourceSpec(path=source, source_id="site-a", iana_timezone="Europe/Paris")]
    )

    result = prepare_uploaded_dvc(tmp_path, spec)
    measurements = pd.read_parquet(
        tmp_path / "dvc_datasets" / result.dataset_id / "measurements.parquet"
    )
    assert str(measurements["timestamp_utc"].min()) == "2025-12-31 23:00:00+00:00"
