from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pandas as pd
import pytest

from openscientist.dvc.ingestion import DVCIngestionError, normalize_type2
from openscientist.dvc.models import DVCImportSpec, DVCSourceSpec, ExportType
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


def _type2_fixture(path: Path, prefix: str, trace_count: int) -> None:
    frame: dict[str, list[object]] = {
        "day": [1, 1],
        "hour": [0, 0],
        "minute": [0, 5],
        "relativeTime": [0, 300],
        f"{prefix}_TIMESTAMP": [
            "2026-01-01T00:00:00+01:00",
            "2026-01-01T00:05:00+01:00",
        ],
    }
    for index in range(1, trace_count + 1):
        frame[f"{prefix}_CAGE{index:03d}"] = [float(index), float(index + 1)]
    frame[f"{prefix}_AVG"] = [1.0, 2.0]
    frame[f"{prefix}_SEM"] = [0.1, 0.2]
    frame[f"{prefix}_QRT"] = [0.5, 0.5]
    frame[f"{prefix}_SAMPLES"] = [trace_count, trace_count]
    pd.DataFrame(frame).to_csv(path, index=False)


def _type1_fixture(path: Path, group: str, trace_count: int = 8) -> None:
    rows: list[dict[str, object]] = []
    for cage in range(1, trace_count + 1):
        for minute in (0, 5):
            row: dict[str, object] = {
                "timestamp": f"2026-01-01T00:{minute:02d}:00+01:00",
                "stop_ts": f"2026-01-01T00:{minute + 5:02d}:00+01:00",
                "group": group,
                "mouse": f"CAGE{cage:03d}",
                "samples": 1200,
                "day": 1,
                "hour": 0,
                "minute": minute,
                "relativeTime": minute * 60,
            }
            row.update({f"v_{electrode}": cage + electrode / 100 for electrode in range(1, 13)})
            rows.append(row)
    pd.DataFrame(rows).to_csv(path, index=False)


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


def test_eight_file_fixture_reconciles_exactly_114_physical_traces(tmp_path: Path) -> None:
    type2_counts = {"A": 10, "B": 24, "C1": 9, "C2": 6, "E1": 17, "E2": 32}
    sources: list[DVCSourceSpec] = []
    for source_id, count in type2_counts.items():
        path = tmp_path / f"{source_id}.csv"
        _type2_fixture(path, source_id, count)
        sources.append(
            DVCSourceSpec(
                path=path,
                source_id=source_id,
                schema_hint=ExportType.TYPE2,
                expected_trace_count=count,
            )
        )
    for source_id in ("D1", "D2"):
        path = tmp_path / f"{source_id}.csv"
        _type1_fixture(path, source_id)
        sources.append(
            DVCSourceSpec(
                path=path,
                source_id=source_id,
                schema_hint=ExportType.TYPE1,
                expected_trace_count=8,
            )
        )

    result = prepare_uploaded_dvc(tmp_path, DVCImportSpec(sources=sources))
    dataset = tmp_path / "dvc_datasets" / result.dataset_id
    measurements = pd.read_parquet(dataset / "measurements.parquet")
    reconciliation = pd.read_parquet(dataset / "cage_reconciliation.parquet")
    reports = json.loads((dataset / "schema_report.json").read_text(encoding="utf-8"))

    assert result.trace_count == 114
    assert measurements["cage_key"].nunique() == 114
    assert len(reconciliation) == 114
    assert reconciliation["status"].eq("reconciled").all()
    assert sum(report["observed_trace_count"] for report in reports) == 114
    assert {report["source_id"] for report in reports} == set(type2_counts) | {"D1", "D2"}


def test_type2_summary_columns_are_never_loaded_as_traces(tmp_path: Path) -> None:
    source = tmp_path / "summaries.csv"
    _type2_fixture(source, "COLLAB", 3)

    result = prepare_uploaded_dvc(
        tmp_path,
        DVCImportSpec(
            sources=[DVCSourceSpec(path=source, source_id="summary-check", expected_trace_count=3)]
        ),
    )
    measurements = pd.read_parquet(
        tmp_path / "dvc_datasets" / result.dataset_id / "measurements.parquet"
    )

    assert set(measurements["cage_id"]) == {"CAGE001", "CAGE002", "CAGE003"}
    assert (
        not measurements["source_trace_id"]
        .str.contains(r"(?:AVG|SEM|QRT|SAMPLES)$", case=False, regex=True)
        .any()
    )


def test_type2_case_ambiguous_timestamp_blocks_fail_closed() -> None:
    frame = pd.DataFrame(
        {
            "GROUP_TIMESTAMP": ["2026-01-01T00:00:00Z"],
            "group_timestamp": ["2026-01-01T00:00:00Z"],
            "GROUP_CAGE001": [1.0],
            "GROUP_AVG": [1.0],
        }
    )

    with pytest.raises(DVCIngestionError, match="ambiguous timestamp blocks"):
        normalize_type2(frame, source_file="ambiguous.csv", metric_name="activity")


def test_prepared_artifact_is_reused_after_process_restart(tmp_path: Path) -> None:
    source = tmp_path / "activity.csv"
    _type2(source)
    spec = DVCImportSpec(
        sources=[DVCSourceSpec(path=source, source_id="site-a", expected_trace_count=2)]
    )
    created = prepare_uploaded_dvc(tmp_path, spec)
    payload = json.dumps(spec.model_dump(mode="json"))
    script = (
        "import json,sys; "
        "from pathlib import Path; "
        "from openscientist.dvc.models import DVCImportSpec; "
        "from openscientist.dvc.preparation import prepare_uploaded_dvc; "
        "spec=DVCImportSpec.model_validate(json.loads(sys.argv[2])); "
        "print(prepare_uploaded_dvc(Path(sys.argv[1]), spec).model_dump_json())"
    )

    completed = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), payload],
        check=True,
        capture_output=True,
        text=True,
    )
    restarted = json.loads(completed.stdout.strip().splitlines()[-1])

    assert restarted["dataset_id"] == created.dataset_id
    assert restarted["measurement_asset_id"] == created.measurement_asset_id
    assert restarted["reused"] is True


def test_prepared_artifact_tampering_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "activity.csv"
    _type2(source)
    spec = DVCImportSpec(
        sources=[DVCSourceSpec(path=source, source_id="site-a", expected_trace_count=2)]
    )
    created = prepare_uploaded_dvc(tmp_path, spec)
    measurements = tmp_path / "dvc_datasets" / created.dataset_id / "measurements.parquet"
    with measurements.open("ab") as handle:
        handle.write(b"tampered")

    with pytest.raises(DVCIngestionError, match="artifact integrity failed"):
        prepare_uploaded_dvc(tmp_path, spec)


def test_concurrent_preparation_publishes_one_atomic_dataset(tmp_path: Path) -> None:
    source = tmp_path / "activity.csv"
    _type2(source)
    spec = DVCImportSpec(
        sources=[DVCSourceSpec(path=source, source_id="site-a", expected_trace_count=2)]
    )
    barrier = Barrier(2)

    def prepare() -> object:
        barrier.wait(timeout=5)
        return prepare_uploaded_dvc(tmp_path, spec)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: prepare(), range(2)))

    assert results[0].dataset_id == results[1].dataset_id
    assert sorted(result.reused for result in results) == [False, True]
    dataset = tmp_path / "dvc_datasets" / results[0].dataset_id
    assert (dataset / "manifest.json").is_file()
    assert pd.read_parquet(dataset / "measurements.parquet")["cage_key"].nunique() == 2
    assert not list((tmp_path / "dvc_datasets").glob(".*.lock"))
    assert not list((tmp_path / "dvc_datasets").glob(".*.staging-*"))
