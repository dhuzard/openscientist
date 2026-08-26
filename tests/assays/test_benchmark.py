from __future__ import annotations

from openscientist.assays.benchmark import (
    BenchmarkCase,
    default_cross_assay_cases,
    run_cross_assay_benchmark,
)


def test_default_cross_assay_benchmark_covers_required_failure_modes() -> None:
    cases = default_cross_assay_cases()
    report = run_cross_assay_benchmark(cases)

    assert report.passed
    assert report.case_count == len(cases)
    assert {case.assay_id for case in cases} == {"dvc", "open-field"}
    assert {case.category for case in cases} == {
        "golden_dataset",
        "missing_metadata",
        "known_simulated_effect",
        "sampling_rate_perturbation",
        "timezone_change",
        "duplicated_subject",
        "unit_of_analysis_trap",
        "deterministic_rerun",
    }
    assert all(outcome.expectation_met for outcome in report.outcomes)


def test_benchmark_report_is_order_invariant_and_digest_deterministic() -> None:
    cases = default_cross_assay_cases()

    forward = run_cross_assay_benchmark(cases)
    reverse = run_cross_assay_benchmark(tuple(reversed(cases)))

    assert forward == reverse
    assert len(forward.sha256) == 64


def test_benchmark_fails_closed_for_unregistered_validator() -> None:
    report = run_cross_assay_benchmark(
        (
            BenchmarkCase(
                case_id="invalid-validator",
                assay_id="dvc",
                category="golden_dataset",
                validator_id="dvc.not_registered",
                payload={},
                expected_pass=True,
            ),
        )
    )

    assert not report.passed
    assert not report.outcomes[0].expectation_met
    assert report.outcomes[0].error == "KeyError"
