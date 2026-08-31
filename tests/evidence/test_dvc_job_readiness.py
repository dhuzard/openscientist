from __future__ import annotations

import csv
from pathlib import Path

from openscientist.evidence.dvc_job_readiness import audit_dvc_job, make_readiness_report


def _write_activity(path: Path, prefix: str, start_minute: int, interval: int) -> None:
    fields = [
        "day",
        "hour",
        "minute",
        "relativeTime",
        f"{prefix}_TIMESTAMP",
        f"{prefix}_AVG",
        f"{prefix}_SEM",
        f"{prefix}_QRT",
        f"{prefix}_SAMPLES",
        *[f"{prefix}_CAGE_{index:02d}" for index in range(1, 12)],
    ]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(fields)
        for row_index in range(3):
            minute = start_minute + row_index * interval
            timestamp = f"2026-08-20T00:{minute:02d}:00.000+0200"
            writer.writerow(
                [0, 0, minute, minute * 60, timestamp, 1, 0, "[]", 11]
                + [row_index + cage for cage in range(11)]
            )


def _write_events(path: Path) -> None:
    path.write_text(
        "group,day,hour,minute,relativeTime,timestamp,cage,rack,position,event\n",
        encoding="utf-8",
    )


def test_audit_enumerates_all_traces_and_fails_closed(tmp_path: Path) -> None:
    job = tmp_path / "job-1"
    data = job / "data"
    data.mkdir(parents=True)
    _write_activity(data / "CohortA_animal_loc__index_smoothed.csv", "A", 0, 1)
    _write_activity(data / "CohortB_animal_loc__index_smoothed.csv", "B", 0, 5)
    _write_events(data / "CohortA_events.csv")
    _write_events(data / "CohortB_events.csv")
    (job / "final_report.md").write_text(
        "The light/dark schedule was inferred from clock time.", encoding="utf-8"
    )

    audit = audit_dvc_job(job)

    assert audit["eligible_for_strict_hcmo_export"] is False
    assert audit["summary"]["observed_trace_columns"] == 22
    assert audit["summary"]["native_interval_seconds"] == [60, 300]
    checks = {item["check"]: item for item in audit["checks"]}
    assert checks["summary_columns_excluded"]["status"] == "PASS"
    assert checks["timestamp_parse_integrity"]["status"] == "PASS"
    assert checks["native_sampling_intervals"]["status"] == "WARN"
    assert checks["per_cage_light_schedule"]["status"] == "UNAVAILABLE"
    assert checks["expected_cage_reconciliation"]["status"] == "UNAVAILABLE"


def test_readiness_report_explains_blocked_export(tmp_path: Path) -> None:
    job = tmp_path / "job-2"
    data = job / "data"
    data.mkdir(parents=True)
    _write_activity(data / "CohortA_animal_loc__index_smoothed.csv", "A", 0, 5)
    _write_events(data / "CohortA_events.csv")

    report = make_readiness_report(audit_dvc_job(job))

    assert "Strict export: **BLOCKED**" in report
    assert "governed_light_schedule_missing" in report
    assert "must not be promoted into canonical evidence entities" in report


def test_relational_inventory_exposes_missing_evidence_links(tmp_path: Path) -> None:
    job = tmp_path / "job-3"
    data = job / "data"
    data.mkdir(parents=True)
    _write_activity(data / "CohortA_animal_loc__index_smoothed.csv", "A", 0, 5)
    _write_events(data / "CohortA_events.csv")
    inventory = {
        "job_id": "job-3",
        "status": "completed",
        "current_iteration": 10,
        "hypotheses": 0,
        "findings": 5,
        "analyses": 11,
        "literature": 17,
        "data_files": 2,
        "finding_hypothesis_links": 0,
        "finding_literature_links": 0,
        "statistical_results_table": False,
        "finding_analyses_table": False,
        "finding_data_files_table": False,
    }

    audit = audit_dvc_job(job, relational_inventory=inventory)
    checks = {item["check"]: item for item in audit["checks"]}

    assert checks["authoritative_job_state"]["status"] == "PASS"
    assert checks["hypothesis_coverage"]["status"] == "UNAVAILABLE"
    assert checks["literature_linkage"]["status"] == "UNAVAILABLE"
    assert checks["authoritative_evidence_links"]["status"] == "UNAVAILABLE"
    assert checks["first_class_statistical_results"]["status"] == "UNAVAILABLE"
