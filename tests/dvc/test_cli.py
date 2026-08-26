from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import pandas as pd

from openscientist.dvc.cli import run


def test_cli_vertical_slice_writes_traceable_bundle(tmp_path: Path) -> None:
    type1 = {
        "day": [0],
        "hour": [14],
        "minute": [0],
        "relativeTime": [50400],
        "timestamp": ["2026-07-14T14:00:00.000-0400"],
        "group": ["Group_0"],
        "cage": ["C1_Control"],
        "samples": [10000],
        "stop_ts": ["2026-07-14T15:00:00.000-0400"],
    }
    for index in range(1, 13):
        type1[f"v_{index}"] = [float(index)]
    type1_path = tmp_path / "type1.csv"
    pd.DataFrame(type1).to_csv(type1_path, index=False)

    events_path = tmp_path / "events.csv"
    pd.DataFrame(
        {
            "group": ["Group_0"],
            "day": [0],
            "hour": [14],
            "minute": [20],
            "relativeTime": [51600],
            "timestamp": ["2026-07-14T14:20:00.000-0400"],
            "cage": ["C1_Control"],
            "rack": ["R1"],
            "position": ["A1"],
            "event": ["REMOVED"],
        }
    ).to_csv(events_path, index=False)

    output = tmp_path / "out"
    run(
        Namespace(
            type1=str(type1_path),
            type2=None,
            events=str(events_path),
            metadata=None,
            metric="activity",
            study_id="fixture",
            animals_per_cage=5,
            expected_frequency_hz=4.0,
            coverage_threshold=0.95,
            output=str(output),
        )
    )
    required = {
        "study_context.json",
        "study_context.schema.json",
        "metadata_assessment.json",
        "analysis_plan.json",
        "plan_violations.json",
        "evidence_ledger.json",
        "type1_normalized.csv",
        "type1_event_annotated.csv",
        "poc_report.md",
    }
    assert required.issubset({path.name for path in output.iterdir()})
    report = (output / "poc_report.md").read_text(encoding="utf-8")
    assert "cage-level" in report
    assert "retain-versus-mask" in report.lower()
