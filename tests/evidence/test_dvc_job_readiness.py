from __future__ import annotations

import csv
from copy import deepcopy
from pathlib import Path
from typing import Any

from openscientist.evidence.dvc_job_readiness import audit_dvc_job, make_readiness_report


def _write_activity(
    path: Path,
    prefix: str,
    start_minute: int,
    interval: int,
    *,
    trace_count: int = 11,
) -> None:
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
        *[f"{prefix}_CAGE_{index:02d}" for index in range(1, trace_count + 1)],
    ]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(fields)
        for row_index in range(3):
            minute = start_minute + row_index * interval
            timestamp = f"2026-08-20T00:{minute:02d}:00.000+0200"
            writer.writerow(
                [0, 0, minute, minute * 60, timestamp, 1, 0, "[]", trace_count]
                + [row_index + cage for cage in range(trace_count)]
            )


def _write_events(path: Path) -> None:
    path.write_text(
        "group,day,hour,minute,relativeTime,timestamp,cage,rack,position,event\n",
        encoding="utf-8",
    )


def _governance_manifest() -> dict[str, Any]:
    cage_template: dict[str, Any] = {
        "timezone": "Europe/Paris",
        "source_utc_offsets": ["+0200"],
        "lights_on": "07:00:00",
        "lights_off": "19:00:00",
        "transition_policy": "instantaneous",
        "schedule_authority": "synthetic demo protocol v1",
    }
    cages = []
    for cohort, prefix in (("CohortA", "A"), ("CohortB", "B")):
        cage = deepcopy(cage_template)
        cage.update(
            {
                "cage_id": f"demo-cage-{prefix.lower()}",
                "source_file": f"{cohort}_animal_loc__index_smoothed.csv",
                "timestamp_field": f"{prefix}_TIMESTAMP",
                "trace_field": f"{prefix}_CAGE_01",
                "subjects": [
                    {
                        "id": f"demo-mouse-{prefix.lower()}",
                        "housing_start": "2026-08-19T00:00:00+02:00",
                        "housing_end": "2026-08-22T00:00:00+02:00",
                    }
                ],
                "enclosure": {
                    "id": f"demo-enclosure-{prefix.lower()}",
                    "width": 30.0,
                    "length": 45.0,
                    "height": 20.0,
                    "dimension_unit_iri": "http://qudt.org/vocab/unit/CentiM",
                },
            }
        )
        cages.append(cage)
    return {
        "contract_version": "1.0.0-demo",
        "authority": {
            "id": "openscientist-synthetic-demo",
            "review_state": "approved",
            "approved_at": "2026-08-31T12:00:00Z",
        },
        "expected_cage_count": 2,
        "cages": cages,
    }


def _relational_inventory(job_id: str) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "status": "completed",
        "current_iteration": 1,
        "hypotheses": 1,
        "findings": 1,
        "analyses": 1,
        "literature": 1,
        "data_files": 2,
        "finding_hypothesis_links": 1,
        "finding_literature_links": 1,
        "statistical_results_table": True,
        "finding_analyses_table": True,
        "finding_data_files_table": True,
    }


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


def test_governance_manifest_mismatch_fails_closed(tmp_path: Path) -> None:
    job = tmp_path / "governed-demo"
    data = job / "data"
    data.mkdir(parents=True)
    _write_activity(
        data / "CohortA_animal_loc__index_smoothed.csv", "A", 0, 1, trace_count=1
    )
    _write_activity(
        data / "CohortB_animal_loc__index_smoothed.csv", "B", 0, 5, trace_count=1
    )
    _write_events(data / "CohortA_events.csv")
    _write_events(data / "CohortB_events.csv")
    governance = _governance_manifest()
    governance["cages"][1]["trace_field"] = "B_CAGE_404"

    audit = audit_dvc_job(
        job,
        relational_inventory=_relational_inventory(job.name),
        governance_manifest=governance,
    )

    assert audit["eligible_for_strict_hcmo_export"] is False
    assert audit["summary"]["fail"] == 2
    checks = {item["check"]: item for item in audit["checks"]}
    assert checks["per_cage_light_schedule"]["status"] == "PASS"
    assert checks["per_cage_timezone"]["status"] == "PASS"
    assert checks["housing_assignment"]["status"] == "PASS"
    assert checks["enclosure_dimensions"]["status"] == "PASS"
    assert checks["expected_cage_reconciliation"]["reason_code"] == "expected_cage_mismatch"


def test_exact_governance_manifest_passes_all_required_checks(tmp_path: Path) -> None:
    job = tmp_path / "governed-demo"
    data = job / "data"
    data.mkdir(parents=True)
    _write_activity(
        data / "CohortA_animal_loc__index_smoothed.csv", "A", 0, 1, trace_count=1
    )
    _write_activity(
        data / "CohortB_animal_loc__index_smoothed.csv", "B", 0, 5, trace_count=1
    )
    _write_events(data / "CohortA_events.csv")
    _write_events(data / "CohortB_events.csv")
    governance = _governance_manifest()

    audit = audit_dvc_job(
        job,
        relational_inventory=_relational_inventory(job.name),
        governance_manifest=governance,
    )

    assert audit["eligible_for_strict_hcmo_export"] is True
    assert audit["summary"]["fail"] == 0
    assert audit["summary"]["unavailable"] == 0
    assert audit["summary"]["warn"] == 1
    report = make_readiness_report(audit)
    assert "Strict export: **ELIGIBLE**" in report
    assert "export may proceed for this snapshot" in report
    assert "export remains blocked" not in report
