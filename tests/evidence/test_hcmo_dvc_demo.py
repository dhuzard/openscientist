from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

from rdflib import Graph

from openscientist.evidence import hcmo_export
from openscientist.evidence.dvc_job_readiness import audit_dvc_job, write_audit

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "examples" / "hcmo_dvc_demo"
JOB = DEMO / "hcmo-dvc-governed-demo"


def _json_object(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def test_governed_two_cage_demo_runs_end_to_end(tmp_path: Path) -> None:
    analysis = subprocess.run(
        [sys.executable, str(DEMO / "analysis.py"), str(JOB / "data")],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(analysis.stdout)
    assert result == {
        "cage_means": {"demo-cage-a": 14.0, "demo-cage-b": 22.0},
        "common_grid_minutes": 5,
        "equal_cage_mean": 18.0,
        "experimental_unit": "physical_cage",
    }

    audit = audit_dvc_job(
        JOB,
        relational_inventory=_json_object(DEMO / "relational-inventory.json"),
        governance_manifest=_json_object(DEMO / "governance-manifest.json"),
    )
    write_audit(audit, tmp_path / "readiness")
    assert audit["eligible_for_strict_hcmo_export"] is True
    assert audit["summary"] == {
        "checks": 18,
        "pass": 17,
        "warn": 1,
        "fail": 0,
        "unavailable": 0,
        "activity_files": 2,
        "event_files": 2,
        "timestamp_groups": 2,
        "observed_trace_columns": 2,
        "native_interval_seconds": [60, 300],
    }

    validation = hcmo_export.export_hcmo_evidence(
        DEMO / "job-snapshot.json",
        tmp_path / "evidence",
        source_root=JOB,
        report_path=JOB / "final_report.md",
    )
    graph = Graph().parse(tmp_path / "evidence" / "evidence.ttl", format="turtle")

    assert validation["valid"] is True
    assert len(set(graph.subjects(hcmo_export.RDF.type, hcmo_export.HCM.MonitoredEnclosure))) == 2
    assert len(set(graph.subjects(hcmo_export.RDF.type, hcmo_export.HCM_OBS.BehaviorObservation))) == 2
    appendix = (tmp_path / "evidence" / "traceability-appendix.md").read_text(
        encoding="utf-8"
    )
    assert "F-DEMO-001" in appendix
    assert "A-DEMO-001" in appendix
    assert "D-DEMO-A" not in appendix
    assert "CohortA_animal_loc__index_smoothed.csv" in appendix

