"""Executable validation contracts for open-field operation results."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from openscientist.assays import ValidationFinding, ValidationResult

VALIDATOR_VERSION = "1.0.0"


def _result(validator_id: str, findings: list[ValidationFinding]) -> ValidationResult:
    return ValidationResult(
        validator_id=validator_id,
        validator_version=VALIDATOR_VERSION,
        passed=all(finding.passed for finding in findings),
        findings=findings,
    )


def _payload_result(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    value = payload.get("result")
    return value if isinstance(value, Mapping) else {}


def validate_import(payload: Mapping[str, Any]) -> ValidationResult:
    findings = [
        ValidationFinding(
            check_id="schema",
            passed=payload.get("schema_id") == "openscientist-open-field-dataset/1.0",
            message="Dataset manifest uses the governed open-field schema.",
            observed=payload.get("schema_id"),
        ),
        ValidationFinding(
            check_id="non_empty",
            passed=isinstance(payload.get("row_count"), int) and payload.get("row_count", 0) > 0,
            message="Normalized tracking contains observations.",
            observed=payload.get("row_count"),
        ),
        ValidationFinding(
            check_id="unit_of_analysis",
            passed=payload.get("analysis_unit") == "subject",
            message="Subjects, not frames or sessions, are the analysis unit.",
            observed=payload.get("analysis_unit"),
        ),
    ]
    return _result("open_field.import", findings)


def validate_sanity(payload: Mapping[str, Any]) -> ValidationResult:
    data = _payload_result(payload)
    findings = [
        ValidationFinding(
            check_id="operation",
            passed=payload.get("operation_id") == "check_data_sanity",
            message="Payload is a sanity-check result.",
            observed=payload.get("operation_id"),
        ),
        ValidationFinding(
            check_id="clock_synchronized",
            passed=data.get("clock_synchronized") is True,
            message="Acquisition clock synchronization was explicitly verified.",
            observed=data.get("clock_synchronized"),
        ),
        ValidationFinding(
            check_id="sampling_rate",
            passed=data.get("sampling_rate_within_tolerance") is True,
            message="Observed session sampling rates match declared frame rate.",
            observed=data.get("session_sampling"),
        ),
        ValidationFinding(
            check_id="unit_of_analysis",
            passed=payload.get("analysis_unit") == "subject",
            message="Sanity results preserve subject-level inference.",
            observed=payload.get("analysis_unit"),
        ),
    ]
    return _result("open_field.sanity", findings)


def validate_distance(payload: Mapping[str, Any]) -> ValidationResult:
    data = _payload_result(payload)
    rows = data.get("subject_distance")
    if isinstance(rows, list) and rows:
        valid_rows = all(
            isinstance(row, Mapping)
            and isinstance(row.get("distance"), (int, float))
            and math.isfinite(float(row["distance"]))
            and float(row["distance"]) >= 0
            for row in rows
        )
    else:
        valid_rows = False
    findings = [
        ValidationFinding(
            check_id="operation",
            passed=payload.get("operation_id") == "summarize_distance",
            message="Payload is a distance result.",
            observed=payload.get("operation_id"),
        ),
        ValidationFinding(
            check_id="unit_of_analysis",
            passed=payload.get("analysis_unit") == "subject",
            message="Distance summaries expose subject-level analysis rows.",
            observed=payload.get("analysis_unit"),
        ),
        ValidationFinding(
            check_id="finite_nonnegative_distance",
            passed=valid_rows,
            message="Every subject distance is finite and non-negative.",
            observed=rows,
        ),
    ]
    return _result("open_field.distance", findings)


def validate_zone_occupancy(payload: Mapping[str, Any]) -> ValidationResult:
    data = _payload_result(payload)
    rows = data.get("subject_zone_occupancy")
    if isinstance(rows, list) and rows:
        valid_rows = all(
            isinstance(row, Mapping)
            and isinstance(row.get("duration_seconds"), (int, float))
            and math.isfinite(float(row["duration_seconds"]))
            and float(row["duration_seconds"]) >= 0
            and isinstance(row.get("proportion"), (int, float))
            and 0 <= float(row["proportion"]) <= 1
            for row in rows
        )
    else:
        valid_rows = False
    findings = [
        ValidationFinding(
            check_id="operation",
            passed=payload.get("operation_id") == "summarize_zone_occupancy",
            message="Payload is a zone-occupancy result.",
            observed=payload.get("operation_id"),
        ),
        ValidationFinding(
            check_id="unit_of_analysis",
            passed=payload.get("analysis_unit") == "subject",
            message="Zone summaries expose subject-level analysis rows.",
            observed=payload.get("analysis_unit"),
        ),
        ValidationFinding(
            check_id="time_weighted_occupancy",
            passed=valid_rows,
            message="Every subject-zone duration and proportion is bounded.",
            observed=rows,
        ),
    ]
    return _result("open_field.zone_occupancy", findings)
