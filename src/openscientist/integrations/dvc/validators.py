"""Executable fail-closed acceptance validators for governed DVC results."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from openscientist.assays import ValidationFinding, ValidationResult

VALIDATOR_VERSION = "1.0.0"


def _result(validator_id: str, findings: list[ValidationFinding]) -> ValidationResult:
    return ValidationResult(
        validator_id=validator_id,
        validator_version=VALIDATOR_VERSION,
        passed=all(finding.passed for finding in findings),
        findings=findings,
    )


def _validation_view(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Select the explicitly named acceptance record without inventing defaults."""

    validation = payload.get("validation")
    if isinstance(validation, Mapping):
        return validation
    result = payload.get("result")
    if isinstance(result, Mapping):
        nested = result.get("validation")
        if isinstance(nested, Mapping):
            return nested
    return payload


def validate_experimental_unit(payload: Mapping[str, Any]) -> ValidationResult:
    data = _validation_view(payload)
    experimental_unit = data.get("experimental_unit")
    observational_unit = data.get("observational_unit")
    analysis_unit = data.get("analysis_unit")
    allowed = {"cage", "physical_cage", "housing_unit"}
    findings = [
        ValidationFinding(
            check_id="experimental_unit_is_cage",
            passed=experimental_unit in allowed,
            message="DVC experimental unit is an explicitly identified physical cage/housing unit.",
            observed=experimental_unit,
        ),
        ValidationFinding(
            check_id="observational_unit_is_cage",
            passed=observational_unit in allowed,
            message="DVC observations remain cage-level and are not attributed to mice.",
            observed=observational_unit,
        ),
        ValidationFinding(
            check_id="analysis_unit_is_cage",
            passed=analysis_unit in allowed,
            message="DVC inferential rows use cages rather than animals, frames, or raw rows.",
            observed=analysis_unit,
        ),
    ]
    return _result("dvc.experimental_unit", findings)


def validate_reconciliation_and_grid(payload: Mapping[str, Any]) -> ValidationResult:
    data = _validation_view(payload)
    expected = data.get("expected_cage_count")
    observed = data.get("observed_cage_count")
    grid_minutes = data.get("common_grid_minutes")
    count_reconciled = (
        isinstance(expected, int)
        and not isinstance(expected, bool)
        and expected > 0
        and isinstance(observed, int)
        and not isinstance(observed, bool)
        and observed == expected
    )
    valid_grid = (
        isinstance(grid_minutes, (int, float))
        and not isinstance(grid_minutes, bool)
        and grid_minutes > 0
    )
    findings = [
        ValidationFinding(
            check_id="cage_count_reconciliation",
            passed=count_reconciled,
            message="Observed physical cage traces reconcile to an independent expected count.",
            observed={"expected": expected, "observed": observed},
        ),
        ValidationFinding(
            check_id="summary_columns_excluded",
            passed=data.get("summary_columns_excluded") is True,
            message="AVG, SEM, quantile, sample-count, and timestamp columns are not cages.",
            observed=data.get("summary_columns_excluded"),
        ),
        ValidationFinding(
            check_id="native_interval_detected",
            passed=data.get("native_interval_detected") is True,
            message="Native sampling interval was detected instead of inferred from a filename.",
            observed=data.get("native_interval_detected"),
        ),
        ValidationFinding(
            check_id="cage_first_common_grid",
            passed=data.get("cage_first_grid") is True and valid_grid,
            message="Each physical cage was independently aggregated to a declared common grid.",
            observed={
                "cage_first_grid": data.get("cage_first_grid"),
                "common_grid_minutes": grid_minutes,
            },
        ),
        ValidationFinding(
            check_id="gap_aware_processing",
            passed=data.get("gap_aware_processing") is True,
            message="Smoothing and aggregation do not bridge missing-data gaps.",
            observed=data.get("gap_aware_processing"),
        ),
    ]
    return _result("dvc.reconciliation_grid", findings)


def validate_biological_time(payload: Mapping[str, Any]) -> ValidationResult:
    data = _validation_view(payload)
    timezone_value = data.get("timezone")
    light_schedule = data.get("light_schedule")
    valid_schedule = (
        isinstance(light_schedule, Mapping)
        and light_schedule.get("lights_on") is not None
        and light_schedule.get("lights_off") is not None
    )
    findings = [
        ValidationFinding(
            check_id="explicit_timezone",
            passed=isinstance(timezone_value, str) and bool(timezone_value.strip()),
            message="A site/cohort timezone is explicitly recorded.",
            observed=timezone_value,
        ),
        ValidationFinding(
            check_id="explicit_light_schedule",
            passed=valid_schedule,
            message="Local lights-on and lights-off are explicitly recorded.",
            observed=light_schedule,
        ),
        ValidationFinding(
            check_id="clock_synchronized",
            passed=data.get("clock_synchronized") is True,
            message="The acquisition clock is synchronized or reconciled to study time.",
            observed=data.get("clock_synchronized"),
        ),
        ValidationFinding(
            check_id="source_time_preserved",
            passed=data.get("source_timestamp_preserved") is True,
            message="Original timestamp text/offset is retained for audit.",
            observed=data.get("source_timestamp_preserved"),
        ),
        ValidationFinding(
            check_id="clock_correction_documented",
            passed=data.get("clock_correction_documented") is True,
            message="Clock/DST corrections, including no correction, are documented.",
            observed=data.get("clock_correction_documented"),
        ),
    ]
    return _result("dvc.biological_time", findings)


def validate_exact_windows(payload: Mapping[str, Any]) -> ValidationResult:
    data = _validation_view(payload)
    rows = data.get("window_qc")
    eligible = (
        [row for row in rows if isinstance(row, Mapping) and row.get("eligible") is True]
        if isinstance(rows, list)
        else []
    )
    valid = bool(eligible) and all(
        row.get("starts_at_documented_lights_on") is True
        and row.get("half_open_interval") is True
        and row.get("complete_biological_days") == 3
        and isinstance(row.get("expected_grid_bins"), int)
        and row.get("actual_grid_bins") == row.get("expected_grid_bins")
        for row in eligible
    )
    findings = [
        ValidationFinding(
            check_id="eligible_exact_windows_present",
            passed=bool(eligible),
            message="At least one eligible exact three-day biological window is present.",
            observed=len(eligible),
        ),
        ValidationFinding(
            check_id="exact_window_boundaries",
            passed=valid,
            message=(
                "Eligible windows start at documented lights-on, use [start, end), contain "
                "three complete biological days, and have the expected grid bins."
            ),
            observed=eligible,
        ),
    ]
    return _result("dvc.exact_windows", findings)


def validate_invariance_and_rerun(payload: Mapping[str, Any]) -> ValidationResult:
    data = _validation_view(payload)
    findings = [
        ValidationFinding(
            check_id="sampling_rate_invariance",
            passed=data.get("sampling_invariance_passed") is True,
            message="Equivalent cage traces at supported native rates produce equivalent results.",
            observed=data.get("sampling_invariance_passed"),
        ),
        ValidationFinding(
            check_id="native_row_duplication_invariance",
            passed=data.get("row_duplication_invariance_passed") is True,
            message="Duplicating native rows within a cage/time bin does not change results.",
            observed=data.get("row_duplication_invariance_passed"),
        ),
        ValidationFinding(
            check_id="deterministic_rerun",
            passed=data.get("deterministic_rerun_passed") is True,
            message="An identical governed request reuses or reproduces identical evidence.",
            observed=data.get("deterministic_rerun_passed"),
        ),
    ]
    return _result("dvc.invariance", findings)


def validate_analysis_basis(payload: Mapping[str, Any]) -> ValidationResult:
    """Benchmark-level DVC basis gate spanning unit, time, grid, and rerun invariants."""

    data = _validation_view(payload)
    expected = data.get("expected_cage_count")
    observed = data.get("observed_cage_count")
    duplicates = data.get("duplicate_cage_ids")
    findings = [
        ValidationFinding(
            check_id="cage_level_inference",
            passed=data.get("experimental_unit") in {"cage", "physical_cage", "housing_unit"}
            and data.get("analysis_unit") in {"cage", "physical_cage", "housing_unit"},
            message="Experimental and analysis units remain physical cages/housing units.",
            observed={
                "experimental_unit": data.get("experimental_unit"),
                "analysis_unit": data.get("analysis_unit"),
            },
        ),
        ValidationFinding(
            check_id="cage_reconciliation",
            passed=isinstance(expected, int)
            and not isinstance(expected, bool)
            and expected > 0
            and observed == expected
            and isinstance(duplicates, list)
            and not duplicates,
            message="Cage counts reconcile and cage identities are unique.",
            observed={"expected": expected, "observed": observed, "duplicates": duplicates},
        ),
        ValidationFinding(
            check_id="common_grid",
            passed=data.get("common_grid") is True,
            message="Cages are independently standardized to a common sampling grid.",
            observed=data.get("common_grid"),
        ),
        ValidationFinding(
            check_id="biological_time",
            passed=isinstance(data.get("timezone"), str)
            and bool(str(data.get("timezone")).strip())
            and data.get("light_schedule_explicit") is True
            and data.get("clock_synchronized") is True,
            message="Timezone, light schedule, and clock synchronization are explicit.",
            observed={
                "timezone": data.get("timezone"),
                "light_schedule_explicit": data.get("light_schedule_explicit"),
                "clock_synchronized": data.get("clock_synchronized"),
            },
        ),
        ValidationFinding(
            check_id="exact_three_day_basis",
            passed=data.get("complete_days") == 3,
            message="The validation basis contains three complete biological days.",
            observed=data.get("complete_days"),
        ),
        ValidationFinding(
            check_id="sampling_invariance",
            passed=data.get("sampling_rate_invariant") is True,
            message="Supported sampling-rate perturbations preserve the cage-first result.",
            observed=data.get("sampling_rate_invariant"),
        ),
        ValidationFinding(
            check_id="deterministic_rerun",
            passed=data.get("deterministic_rerun") is True,
            message="Identical governed inputs produce or reuse identical evidence.",
            observed=data.get("deterministic_rerun"),
        ),
    ]
    return _result("dvc.analysis_basis", findings)


def validate_known_effect(payload: Mapping[str, Any]) -> ValidationResult:
    data = _validation_view(payload)
    direction = data.get("expected_direction")
    effect = data.get("observed_effect")
    tolerance = data.get("tolerance")
    numeric = (
        isinstance(effect, (int, float))
        and not isinstance(effect, bool)
        and isinstance(tolerance, (int, float))
        and not isinstance(tolerance, bool)
        and tolerance >= 0
    )
    direction_matches = False
    if numeric:
        effect_value = float(cast(int | float, effect))
        tolerance_value = float(cast(int | float, tolerance))
        if direction == "increase":
            direction_matches = effect_value > tolerance_value
        elif direction == "decrease":
            direction_matches = effect_value < -tolerance_value
        elif direction == "no_change":
            direction_matches = abs(effect_value) <= tolerance_value
    findings = [
        ValidationFinding(
            check_id="known_effect_direction",
            passed=direction_matches,
            message="A simulated known effect is recovered in the declared direction.",
            observed={
                "expected_direction": direction,
                "observed_effect": effect,
                "tolerance": tolerance,
            },
        )
    ]
    return _result("dvc.known_effect", findings)
