"""Guarded DVC planning, evidence lineage, UDWA execution boundary and POC scoring."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol

import pandas as pd

from openscientist.dvc.models import (
    AnalysisPlan,
    AnalysisPlanStep,
    DecisionRecord,
    EvidenceLedger,
    EvidenceRecord,
    MetadataAssessment,
    ScientificClaim,
    StudyContext,
    ValueStatus,
)


class AnalysisExecutor(Protocol):
    """Boundary implemented by UDWA, MCP, or a deterministic remote service."""

    def execute(self, tool_name: str, table: pd.DataFrame, parameters: dict[str, Any]) -> Any: ...


def _unknown(value: Any) -> bool:
    return value.status is ValueStatus.UNKNOWN


def propose_plan(context: StudyContext, assessment: MetadataAssessment) -> AnalysisPlan:
    group_known = all(not _unknown(cage.biological_group) for cage in context.cages)
    light_known = not _unknown(context.environment.light_on) and not _unknown(
        context.environment.light_off
    )
    steps = [
        AnalysisPlanStep(
            step_id="inspect",
            title="Inspect and normalize exports",
            rationale="Establish the immutable technical data contract.",
            tool_name="dvc.inspect_exports",
        ),
        AnalysisPlanStep(
            step_id="aggregation-check",
            title="Validate Type 1 against Type 2",
            rationale="Verify that cage summaries reproduce the electrode-level representation.",
            tool_name="dvc.validate_type1_type2",
        ),
        AnalysisPlanStep(
            step_id="qc",
            title="Run coverage and event-aware QC",
            rationale="Find missing intervals and handling-associated disturbances before interpretation.",
            tool_name="udwa.quality_report",
        ),
        AnalysisPlanStep(
            step_id="describe",
            title="Describe cage-level activity and heterogeneity",
            rationale="The fixture supports descriptive cage-level exploration without biological assignment.",
            tool_name="udwa.summarize_activity",
            required_metadata=["units.experimental_unit"],
        ),
        AnalysisPlanStep(
            step_id="light-dark",
            title="Summarize light and dark phases",
            rationale="Photoperiod can explain temporal structure.",
            tool_name="udwa.summarize_light_dark",
            required_metadata=["environment.light_on", "environment.light_off"],
            status="planned" if light_known else "blocked",
            blocked_reason=None if light_known else "light schedule is unresolved",
        ),
        AnalysisPlanStep(
            step_id="group-comparison",
            title="Compare governed biological groups",
            rationale="Only explicit assignments support a biological contrast.",
            tool_name="udwa.compare_window_summaries",
            required_metadata=["cages[].biological_group"],
            approval_required=True,
            status="planned" if group_known else "blocked",
            blocked_reason=None if group_known else "biological group meanings are unresolved",
        ),
        AnalysisPlanStep(
            step_id="report",
            title="Generate evidence-linked exploratory report",
            rationale="Separate recorded facts, computations, inferences and unresolved uncertainty.",
            tool_name="dvc.render_traceable_report",
            approval_required=True,
        ),
    ]
    active_block = any(step.status == "blocked" and step.step_id in {"inspect", "qc", "describe"} for step in steps)
    return AnalysisPlan(
        objective=context.objective.value or "Unresolved DVC analysis objective",
        scope=context.mode,
        steps=steps,
        assumptions=[
            "Cage is the experimental and observational unit for this POC.",
            "Cage labels are opaque until explicit group metadata are supplied.",
            "All outputs are exploratory; deterministic UDWA tools compute numerical results.",
        ],
        status="blocked" if active_block or not assessment.ready_for_descriptive_analysis else "draft",
    )


def validate_plan(plan: AnalysisPlan, context: StudyContext) -> list[str]:
    violations: list[str] = []
    for step in plan.steps:
        if step.status == "blocked":
            continue
        text = f"{step.title} {step.rationale} {step.tool_name}".lower()
        if "per-animal" in text or "per animal" in text:
            violations.append(f"{step.step_id}: cage-level signal cannot be silently attributed to animals")
        if "group" in text and "compare" in text and any(
            _unknown(cage.biological_group) for cage in context.cages
        ):
            violations.append(f"{step.step_id}: biological groups are unresolved")
        if ("light" in text or "zt" in text) and (
            _unknown(context.environment.light_on) or _unknown(context.environment.light_off)
        ):
            violations.append(f"{step.step_id}: photoperiod is unresolved")
        if "exclude" in text and not step.approval_required:
            violations.append(f"{step.step_id}: exclusion decisions require approval")
    return violations


def adapt_plan_for_event_qc(plan: AnalysisPlan, low_coverage_event_bins: int) -> AnalysisPlan:
    out = plan.model_copy(deep=True)
    if low_coverage_event_bins <= 0 or any(step.step_id == "event-sensitivity" for step in out.steps):
        return out
    sensitivity = AnalysisPlanStep(
        step_id="event-sensitivity",
        title="Retain-versus-mask event sensitivity analysis",
        rationale=(
            f"QC found {low_coverage_event_bins} low-coverage bins containing events; compare results "
            "with bins retained and with an approved event mask."
        ),
        tool_name="udwa.compare_event_mask_sensitivity",
        approval_required=True,
    )
    report_index = next((index for index, step in enumerate(out.steps) if step.step_id == "report"), len(out.steps))
    out.steps.insert(report_index, sensitivity)
    return out


def add_evidence(
    ledger: EvidenceLedger,
    *,
    kind: str,
    source: str,
    description: str,
    payload: dict[str, Any] | None = None,
    sha256: str | None = None,
) -> str:
    canonical = json.dumps(payload or {}, sort_keys=True, default=str)
    seed = f"{kind}|{source}|{description}|{canonical}".encode()
    evidence_id = f"ev-{hashlib.sha256(seed).hexdigest()[:12]}"
    ledger.evidence.append(
        EvidenceRecord(
            evidence_id=evidence_id,
            kind=kind,  # type: ignore[arg-type]
            source=source,
            description=description,
            payload=payload,
            sha256=sha256,
        )
    )
    return evidence_id


def add_claim(
    ledger: EvidenceLedger,
    *,
    text: str,
    kind: str,
    evidence_ids: list[str],
    limitations: list[str] | None = None,
) -> str:
    claim_id = f"claim-{hashlib.sha256(text.encode()).hexdigest()[:12]}"
    ledger.claims.append(
        ScientificClaim(
            claim_id=claim_id,
            text=text,
            kind=kind,  # type: ignore[arg-type]
            evidence_ids=evidence_ids,
            limitations=limitations or [],
        )
    )
    EvidenceLedger.model_validate(ledger.model_dump())
    return claim_id


def add_decision(
    ledger: EvidenceLedger,
    *,
    issue: str,
    action: str,
    rationale: str,
    evidence_ids: list[str],
) -> str:
    decision_id = f"decision-{hashlib.sha256((issue + action).encode()).hexdigest()[:12]}"
    ledger.decisions.append(
        DecisionRecord(
            decision_id=decision_id,
            issue=issue,
            proposed_action=action,
            rationale=rationale,
            evidence_ids=evidence_ids,
        )
    )
    EvidenceLedger.model_validate(ledger.model_dump())
    return decision_id


EVALUATION_WEIGHTS = {
    "scientific_correctness_and_restraint": 30,
    "metadata_intelligence": 25,
    "agentic_workflow": 20,
    "traceability_and_reproducibility": 15,
    "user_and_product_value": 10,
}

MANDATORY_GATES = (
    "numerical fidelity with UDWA for identical approved parameters",
    "no vendor metric equivalence without a versioned contract and conformance evidence",
    "no unsupported biological grouping",
    "no individual-animal attribution from group-housed cage signals",
    "no ZT or light/dark interpretation without a light schedule",
    "no silent exclusions",
    "no silent metadata invention",
    "claim-level lineage",
    "repeatable deterministic outputs",
)


def score_poc(category_percentages: dict[str, float], gate_results: dict[str, bool]) -> dict[str, Any]:
    missing = set(EVALUATION_WEIGHTS) - set(category_percentages)
    if missing:
        raise ValueError(f"missing category scores: {sorted(missing)}")
    total = sum(
        EVALUATION_WEIGHTS[name] * max(0.0, min(100.0, category_percentages[name])) / 100
        for name in EVALUATION_WEIGHTS
    )
    failed_gates = [gate for gate in MANDATORY_GATES if not gate_results.get(gate, False)]
    low_categories = [name for name, score in category_percentages.items() if score < 75]
    passed = not failed_gates and not low_categories and total >= 85
    return {
        "total_score": round(total, 2),
        "passed": passed,
        "failed_gates": failed_gates,
        "categories_below_75_percent": low_categories,
    }


def metadata_agent_uplifts(
    *, udwa_m0: float, udwa_m2: float, agent_m0: float, agent_m2: float
) -> dict[str, float]:
    return {
        "metadata_uplift": agent_m2 - agent_m0,
        "agentic_uplift": agent_m2 - udwa_m2,
        "metadata_agent_interaction": (agent_m2 - agent_m0) - (udwa_m2 - udwa_m0),
    }
