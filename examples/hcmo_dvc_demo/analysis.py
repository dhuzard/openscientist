#!/usr/bin/env python3
"""Compute the governed demo's equal-cage mean on a five-minute grid."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path


def cage_mean(path: Path, timestamp_field: str, trace_field: str) -> float:
    bins: dict[datetime, list[float]] = defaultdict(list)
    with path.open("r", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            timestamp = datetime.fromisoformat(row[timestamp_field])
            time_bin = timestamp.replace(minute=(timestamp.minute // 5) * 5, second=0)
            bins[time_bin].append(float(row[trace_field]))
    if not bins:
        raise ValueError(f"no observations in {path}")
    return statistics.mean(statistics.mean(values) for values in bins.values())


def analyze(data_dir: Path) -> dict[str, object]:
    cage_means = {
        "demo-cage-a": cage_mean(
            data_dir / "CohortA_animal_loc__index_smoothed.csv",
            "A_TIMESTAMP",
            "A_CAGE_01",
        ),
        "demo-cage-b": cage_mean(
            data_dir / "CohortB_animal_loc__index_smoothed.csv",
            "B_TIMESTAMP",
            "B_CAGE_01",
        ),
    }
    return {
        "common_grid_minutes": 5,
        "cage_means": cage_means,
        "equal_cage_mean": statistics.mean(cage_means.values()),
        "experimental_unit": "physical_cage",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data_dir", type=Path)
    print(json.dumps(analyze(parser.parse_args().data_dir), sort_keys=True))


if __name__ == "__main__":
    main()

