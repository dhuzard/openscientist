from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from openscientist.dvc.ingestion import (
    annotate_event_counts,
    detect_export_type,
    normalize_events,
    normalize_type1,
    normalize_type2,
    validate_type1_against_type2,
    validate_type2_group_statistics,
)
from openscientist.dvc.metadata import (
    ablate_context,
    apply_known_poc_context,
    assess_metadata,
    context_from_udwa_bundle,
)
from openscientist.dvc.models import ContextValue, EvidenceLedger, ExportType, ValueStatus
from openscientist.dvc.workflow import (
    MANDATORY_GATES,
    add_claim,
    add_evidence,
    adapt_plan_for_event_qc,
    metadata_agent_uplifts,
    propose_plan,
    score_poc,
    validate_plan,
)


def type1_frame() -> pd.DataFrame:
    values = {
        "day": [0, 0],
        "hour": [14, 15],
        "minute": [0, 0],
        "relativeTime": [50400, 54000],
        "timestamp": ["2026-07-14T14:00:00.000-0400", "2026-07-14T15:00:00.000-0400"],
        "group": ["Group_0", "Group_0"],
        "cage": ["C1_Control", "C1_Control"],
        "samples": [14400, 10000],
        "stop_ts": ["2026-07-14T15:00:00.000-0400", "2026-07-14T16:00:00.000-0400"],
    }
    for index in range(1, 13):
        values[f"v_{index}"] = [float(index), float(index + 1)]
    return pd.DataFrame(values)


def type2_frame() -> pd.DataFrame:
    first = float(np.mean(range(1, 13)))
    second = float(np.mean(range(2, 14)))
    return pd.DataFrame(
        {
            "day": [0, 0],
            "hour": [14, 15],
            "minute": [0, 0],
            "relativeTime": [50400, 54000],
            "Group_0_TIMESTAMP": [
                "2026-07-14T14:00:00.000-0400",
                "2026-07-14T15:00:00.000-0400",
            ],
            "Group_0_AVG": [first, second],
            "Group_0_SEM": [np.nan, np.nan],
            "Group_0_QRT": ["[]", "[]"],
            "Group_0_SAMPLES": [14400, 10000],
            "Group_0_C1_Control": [first, second],
        }
    )


def event_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "group": ["Group_0"],
            "day": [0],
            "hour": [15],
            "minute": [10],
            "relativeTime": [54600],
            "timestamp": ["2026-07-14T15:10:00.000-0400"],
            "cage": ["C1_Control"],
            "rack": ["25001304A"],
            "position": ["A4"],
            "event": ["REMOVED"],
        }
    )


def test_context_value_enforces_epistemic_state() -> None:
    with pytest.raises(ValidationError):
        ContextValue[str](value="cage", status=ValueStatus.UNKNOWN)
    with pytest.raises(ValidationError):
        ContextValue[str](value="cage", status=ValueStatus.INFERRED)
    assert ContextValue[str].recorded("cage", "expert").value == "cage"


def test_detect_and_normalize_export_contracts() -> None:
    assert detect_export_type(type1_frame().columns) is ExportType.TYPE1
    assert detect_export_type(type2_frame().columns) is ExportType.TYPE2
    assert detect_export_type(event_frame().columns) is ExportType.EVENTS
    type1, inspection = normalize_type1(
        type1_frame(), source_file="type1.csv", metric_name="activity"
    )
    assert inspection.cage_ids == ["C1_Control"]
    assert inspection.native_bin_seconds == 3600
    assert type1.loc[0, "value"] == pytest.approx(6.5)
    assert type1.loc[1, "coverage_fraction"] == pytest.approx(10000 / 14400)


def test_type1_and_type2_values_are_cross_checked() -> None:
    type1, _ = normalize_type1(type1_frame(), source_file="t1.csv", metric_name="activity")
    type2, _ = normalize_type2(type2_frame(), source_file="t2.csv", metric_name="activity")
    result = validate_type1_against_type2(type1, type2)
    assert result.compared_rows == 2
    assert result.matched_rows == 2
    assert result.unmatched_rows == 0


def test_vendor_sem_contract_distinguishes_sem_from_sd() -> None:
    frame = pd.DataFrame(
        {
            "day": [0],
            "hour": [14],
            "minute": [0],
            "relativeTime": [50400],
            "Group_0_TIMESTAMP": ["2026-07-14T14:00:00.000-0400"],
            "Group_0_AVG": [2.0],
            "Group_0_SEM": [1.0],
            "Group_0_QRT": ["[]"],
            "Group_0_SAMPLES": [100],
            "Group_0_C1": [1.0],
            "Group_0_C2": [2.0],
            "Group_0_C3": [3.0],
        }
    )
    normalized, _ = normalize_type2(frame, source_file="t2.csv", metric_name="activity")
    result = validate_type2_group_statistics(normalized)
    assert result.average_matches == 1
    assert result.sem_matches_sample_sd == 1
    assert result.sem_matches_conventional == 0


def test_event_annotation_is_interval_and_cage_specific() -> None:
    type1, _ = normalize_type1(type1_frame(), source_file="t1.csv", metric_name="activity")
    events, _ = normalize_events(event_frame(), source_file="events.csv")
    annotated = annotate_event_counts(type1, events)
    assert annotated["event_count"].tolist() == [0, 1]
    assert annotated.loc[1, "event_codes"] == "REMOVED"


def test_udwa_adapter_does_not_parse_cage_labels_as_groups() -> None:
    context = context_from_udwa_bundle({}, study_id="fixture", detected_cages=["C1_Control"])
    assert context.cages[0].biological_group.status is ValueStatus.UNKNOWN
    context = apply_known_poc_context(context)
    assert context.units.experimental_unit.value == "cage"
    assert context.cages[0].animals_per_cage.value == 5


def test_assessment_prioritizes_consequential_questions() -> None:
    context = apply_known_poc_context(
        context_from_udwa_bundle({}, study_id="fixture", detected_cages=["C1_Control"])
    )
    assessment = assess_metadata(context)
    assert assessment.ready_for_qc
    assert assessment.ready_for_descriptive_analysis
    assert len(assessment.prioritized_questions) <= 5
    assert any("labels" in gap.rationale.lower() for gap in assessment.gaps)
    assert "biological group comparison" in assessment.blocked_analyses


def test_ablations_remove_only_selected_context() -> None:
    context = apply_known_poc_context(
        context_from_udwa_bundle({}, study_id="fixture", detected_cages=["C1"])
    )
    ablated = ablate_context(context, "A2_unit_of_inference")
    assert ablated.units.experimental_unit.status is ValueStatus.UNKNOWN
    assert ablated.objective.value == context.objective.value
    with pytest.raises(ValueError):
        ablate_context(context, "A99")


def test_plan_blocks_unsupported_analyses_without_guardrail_violations() -> None:
    context = apply_known_poc_context(
        context_from_udwa_bundle({}, study_id="fixture", detected_cages=["C1_Control"])
    )
    plan = propose_plan(context, assess_metadata(context))
    status = {step.step_id: step.status for step in plan.steps}
    assert status["group-comparison"] == "blocked"
    assert status["light-dark"] == "blocked"
    assert validate_plan(plan, context) == []


def test_event_qc_adapts_the_plan_once() -> None:
    context = apply_known_poc_context(
        context_from_udwa_bundle({}, study_id="fixture", detected_cages=["C1"])
    )
    plan = propose_plan(context, assess_metadata(context))
    adapted = adapt_plan_for_event_qc(plan, 3)
    assert [step.step_id for step in adapted.steps].count("event-sensitivity") == 1
    assert adapted.steps[-2].step_id == "event-sensitivity"
    assert [step.step_id for step in adapt_plan_for_event_qc(adapted, 3).steps].count(
        "event-sensitivity"
    ) == 1


def test_evidence_ledger_enforces_claim_lineage() -> None:
    ledger = EvidenceLedger()
    evidence_id = add_evidence(
        ledger,
        kind="metadata",
        source="context.json",
        description="context",
        payload={"experimental_unit": "cage"},
    )
    add_claim(
        ledger,
        text="The experimental unit is the cage.",
        kind="recorded_fact",
        evidence_ids=[evidence_id],
    )
    assert len(ledger.claims) == 1
    with pytest.raises(ValidationError):
        add_claim(
            ledger,
            text="Unsupported claim",
            kind="agent_inference",
            evidence_ids=["missing"],
        )


def test_poc_score_has_non_compensable_gates() -> None:
    categories = {
        "scientific_correctness_and_restraint": 90,
        "metadata_intelligence": 90,
        "agentic_workflow": 90,
        "traceability_and_reproducibility": 90,
        "user_and_product_value": 90,
    }
    gates = {gate: True for gate in MANDATORY_GATES}
    assert score_poc(categories, gates)["passed"]
    gates[MANDATORY_GATES[0]] = False
    assert not score_poc(categories, gates)["passed"]


def test_uplifts_separate_metadata_and_agentic_effects() -> None:
    result = metadata_agent_uplifts(udwa_m0=50, udwa_m2=60, agent_m0=65, agent_m2=90)
    assert result == {
        "metadata_uplift": 25,
        "agentic_uplift": 30,
        "metadata_agent_interaction": 15,
    }
