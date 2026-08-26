"""Deterministic cross-assay validation benchmark runner."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from openscientist.assays.registry import AssayRegistry, get_assay_registry
from openscientist.assays.workflow import canonical_json_sha256

BenchmarkCategory = Literal[
    "golden_dataset",
    "missing_metadata",
    "known_simulated_effect",
    "sampling_rate_perturbation",
    "timezone_change",
    "duplicated_subject",
    "unit_of_analysis_trap",
    "deterministic_rerun",
]


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    assay_id: str
    category: BenchmarkCategory
    validator_id: str
    payload: Mapping[str, Any]
    expected_pass: bool


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BenchmarkOutcome(_StrictModel):
    case_id: str
    assay_id: str
    category: BenchmarkCategory
    validator_id: str
    expected_pass: bool
    observed_pass: bool
    expectation_met: bool
    finding_ids: tuple[str, ...] = ()
    error: str | None = None


class BenchmarkReport(_StrictModel):
    schema_id: str = "openscientist-cross-assay-benchmark/1.0"
    passed: bool
    case_count: int = Field(ge=0)
    outcomes: tuple[BenchmarkOutcome, ...]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def run_cross_assay_benchmark(
    cases: Sequence[BenchmarkCase],
    *,
    registry: AssayRegistry | None = None,
) -> BenchmarkReport:
    """Run versioned adapter validators and compare with explicit expectations."""

    assay_registry = registry or get_assay_registry()
    outcomes: list[BenchmarkOutcome] = []
    for case in sorted(cases, key=lambda item: (item.assay_id, item.case_id)):
        try:
            adapter = assay_registry.require(case.assay_id)
            validator = adapter.validators[case.validator_id]
            result = validator(case.payload)
            observed = result.passed
            outcomes.append(
                BenchmarkOutcome(
                    case_id=case.case_id,
                    assay_id=case.assay_id,
                    category=case.category,
                    validator_id=case.validator_id,
                    expected_pass=case.expected_pass,
                    observed_pass=observed,
                    expectation_met=observed == case.expected_pass,
                    finding_ids=tuple(item.check_id for item in result.findings),
                )
            )
        except Exception as exc:  # fail closed and retain a stable error class
            outcomes.append(
                BenchmarkOutcome(
                    case_id=case.case_id,
                    assay_id=case.assay_id,
                    category=case.category,
                    validator_id=case.validator_id,
                    expected_pass=case.expected_pass,
                    observed_pass=False,
                    expectation_met=False,
                    error=type(exc).__name__,
                )
            )
    canonical = [item.model_dump(mode="json") for item in outcomes]
    return BenchmarkReport(
        passed=all(item.expectation_met for item in outcomes),
        case_count=len(outcomes),
        outcomes=tuple(outcomes),
        sha256=canonical_json_sha256({"outcomes": canonical}),
    )


def default_cross_assay_cases() -> tuple[BenchmarkCase, ...]:
    """Return small golden and adversarial fixtures spanning DVC and open field."""

    dvc_good: dict[str, Any] = {
        "experimental_unit": "cage",
        "analysis_unit": "cage",
        "expected_cage_count": 4,
        "observed_cage_count": 4,
        "common_grid": True,
        "timezone": "Europe/Paris",
        "light_schedule_explicit": True,
        "clock_synchronized": True,
        "complete_days": 3,
        "sampling_rate_invariant": True,
        "duplicate_cage_ids": [],
        "deterministic_rerun": True,
    }
    open_field_result: dict[str, Any] = {
        "clock_synchronized": True,
        "sampling_rate_within_tolerance": True,
        "session_sampling": [{"observed_frame_rate_hz": 10.0}],
    }
    open_field_good: dict[str, Any] = {
        "operation_id": "check_data_sanity",
        "analysis_unit": "subject",
        "result": open_field_result,
    }
    return (
        BenchmarkCase(
            "dvc-golden",
            "dvc",
            "golden_dataset",
            "dvc.analysis_basis",
            dvc_good,
            True,
        ),
        BenchmarkCase(
            "dvc-missing-metadata",
            "dvc",
            "missing_metadata",
            "dvc.analysis_basis",
            {**dvc_good, "timezone": None},
            False,
        ),
        BenchmarkCase(
            "dvc-known-effect",
            "dvc",
            "known_simulated_effect",
            "dvc.known_effect",
            {"expected_direction": "increase", "observed_effect": 1.5, "tolerance": 0.01},
            True,
        ),
        BenchmarkCase(
            "dvc-sampling-perturbation",
            "dvc",
            "sampling_rate_perturbation",
            "dvc.analysis_basis",
            {**dvc_good, "source_sampling_seconds": [1, 60, 900]},
            True,
        ),
        BenchmarkCase(
            "dvc-timezone-change",
            "dvc",
            "timezone_change",
            "dvc.analysis_basis",
            {**dvc_good, "timezone": "America/New_York"},
            True,
        ),
        BenchmarkCase(
            "dvc-duplicate-cage",
            "dvc",
            "duplicated_subject",
            "dvc.analysis_basis",
            {**dvc_good, "duplicate_cage_ids": ["cage-1"]},
            False,
        ),
        BenchmarkCase(
            "dvc-unit-trap",
            "dvc",
            "unit_of_analysis_trap",
            "dvc.analysis_basis",
            {**dvc_good, "analysis_unit": "measurement"},
            False,
        ),
        BenchmarkCase(
            "dvc-deterministic",
            "dvc",
            "deterministic_rerun",
            "dvc.analysis_basis",
            dvc_good,
            True,
        ),
        BenchmarkCase(
            "open-field-golden",
            "open-field",
            "golden_dataset",
            "open_field.sanity",
            open_field_good,
            True,
        ),
        BenchmarkCase(
            "open-field-missing-clock",
            "open-field",
            "missing_metadata",
            "open_field.sanity",
            {
                **open_field_good,
                "result": {
                    **open_field_result,
                    "clock_synchronized": None,
                },
            },
            False,
        ),
        BenchmarkCase(
            "open-field-sampling-mismatch",
            "open-field",
            "sampling_rate_perturbation",
            "open_field.sanity",
            {
                **open_field_good,
                "result": {
                    **open_field_result,
                    "sampling_rate_within_tolerance": False,
                },
            },
            False,
        ),
        BenchmarkCase(
            "open-field-unit-trap",
            "open-field",
            "unit_of_analysis_trap",
            "open_field.sanity",
            {**open_field_good, "analysis_unit": "frame"},
            False,
        ),
        BenchmarkCase(
            "open-field-known-effect",
            "open-field",
            "known_simulated_effect",
            "open_field.distance",
            {
                "operation_id": "summarize_distance",
                "analysis_unit": "subject",
                "result": {"subject_distance": [{"subject_id": "s1", "distance": 4.0}]},
            },
            True,
        ),
        BenchmarkCase(
            "open-field-deterministic",
            "open-field",
            "deterministic_rerun",
            "open_field.distance",
            {
                "operation_id": "summarize_distance",
                "analysis_unit": "subject",
                "result": {"subject_distance": [{"subject_id": "s1", "distance": 4.0}]},
            },
            True,
        ),
    )
