from __future__ import annotations

from openscientist.integrations.dvc.adapter import DVC_ADAPTER


def valid_payload():
    return {
        "validation": {
            "experimental_unit": "physical_cage",
            "observational_unit": "cage",
            "analysis_unit": "housing_unit",
            "expected_cage_count": 4,
            "observed_cage_count": 4,
            "summary_columns_excluded": True,
            "native_interval_detected": True,
            "cage_first_grid": True,
            "common_grid_minutes": 5,
            "gap_aware_processing": True,
            "timezone": "Europe/Paris",
            "light_schedule": {"lights_on": "07:00", "lights_off": "19:00"},
            "clock_synchronized": True,
            "source_timestamp_preserved": True,
            "clock_correction_documented": True,
            "window_qc": [
                {
                    "eligible": True,
                    "starts_at_documented_lights_on": True,
                    "half_open_interval": True,
                    "complete_biological_days": 3,
                    "expected_grid_bins": 864,
                    "actual_grid_bins": 864,
                }
            ],
            "sampling_invariance_passed": True,
            "row_duplication_invariance_passed": True,
            "deterministic_rerun_passed": True,
            "common_grid": True,
            "light_schedule_explicit": True,
            "complete_days": 3,
            "sampling_rate_invariant": True,
            "duplicate_cage_ids": [],
            "deterministic_rerun": True,
        }
    }


def test_all_registered_dvc_acceptance_validators_pass_a_complete_record():
    payload = valid_payload()

    results = [
        validator(payload)
        for validator_id, validator in DVC_ADAPTER.validators.items()
        if validator_id != "dvc.known_effect"
    ]

    assert results
    assert all(result.passed for result in results)
    referenced = {
        validator_id
        for contract in DVC_ADAPTER.operation_contracts.values()
        for validator_id in contract.validator_ids
    }
    assert {
        "dvc.analysis_basis",
        "dvc.experimental_unit",
        "dvc.reconciliation_grid",
        "dvc.biological_time",
        "dvc.exact_windows",
        "dvc.invariance",
    } <= referenced
    assert "dvc.known_effect" in DVC_ADAPTER.validators
    assert DVC_ADAPTER.validators["dvc.known_effect"](
        {"expected_direction": "increase", "observed_effect": 1.5, "tolerance": 0.01}
    ).passed


def test_dvc_validators_fail_closed_on_unit_counts_time_windows_and_invariance():
    payload = valid_payload()
    data = payload["validation"]
    data.update(
        {
            "analysis_unit": "mouse",
            "observed_cage_count": 3,
            "clock_synchronized": False,
            "window_qc": [],
            "sampling_invariance_passed": False,
        }
    )

    results = {
        validator_id: validator(payload)
        for validator_id, validator in DVC_ADAPTER.validators.items()
    }

    assert not results["dvc.experimental_unit"].passed
    assert not results["dvc.reconciliation_grid"].passed
    assert not results["dvc.biological_time"].passed
    assert not results["dvc.exact_windows"].passed
    assert not results["dvc.invariance"].passed


def test_dvc_validators_do_not_treat_missing_acceptance_evidence_as_success():
    assert all(not validator({}).passed for validator in DVC_ADAPTER.validators.values())
