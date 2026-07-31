"""Command-line vertical slice for the metadata-aware DVC proof of concept."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from openscientist.dvc.ingestion import (
    annotate_event_counts,
    file_sha256,
    normalize_events,
    normalize_type1,
    normalize_type2,
    read_csv,
    validate_type1_against_type2,
    validate_type2_group_statistics,
)
from openscientist.dvc.metadata import (
    add_acquisition_context,
    apply_known_poc_context,
    assess_metadata,
    context_from_udwa_bundle,
    load_udwa_bundle,
    register_asset,
)
from openscientist.dvc.models import EvidenceLedger
from openscientist.dvc.workflow import (
    adapt_plan_for_event_qc,
    add_claim,
    add_decision,
    add_evidence,
    propose_plan,
    validate_plan,
)


def _json(path: Path, value: Any) -> None:
    if hasattr(value, "model_dump_json"):
        path.write_text(value.model_dump_json(indent=2), encoding="utf-8")
    else:
        path.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")


def _report(
    *,
    context: Any,
    assessment: Any,
    plan: Any,
    inspections: list[Any],
    aggregation: Any | None,
    group_stats: Any | None,
    low_coverage_bins: int,
    low_coverage_event_bins: int,
) -> str:
    lines = [
        f"# DVC metadata-aware POC report — {context.study_id}",
        "",
        "## Scope and scientific restraint",
        "",
        context.objective.value or "The study objective is unresolved.",
        "",
        "The native experimental and observational entity is the cage. Cage labels are treated as "
        "opaque identifiers unless explicit biological assignments are supplied. This report is "
        "exploratory and does not make individual-animal, treatment-effect, or causal claims.",
        "",
        "## Registered context",
        "",
        f"- Metadata condition: `{context.metadata_level.value}`",
        f"- Experimental unit: `{context.units.experimental_unit.value}`",
        f"- Observation unit: `{context.units.observation_unit.value}`",
        f"- Cages: {len(context.cages)}",
        f"- Metric label: `{context.acquisition.metric_name.value}`",
        f"- Source UTC offset: `{context.environment.source_utc_offset.value or 'unresolved'}`",
        "",
        "## Export inspection",
        "",
    ]
    for item in inspections:
        lines.append(
            f"- `{item.source_file}`: {item.export_type.value}, {item.row_count} normalized rows, "
            f"{len(item.cage_ids)} cages, native bin {item.native_bin_seconds or 'unknown'} s"
        )
    lines.extend(["", "## Metadata gaps and questions", ""])
    lines.append(f"Metadata readiness score: **{assessment.quality_score:.1f}/100**")
    for gap in assessment.gaps:
        lines.append(f"- **{gap.field_path}** ({gap.level.value}): {gap.rationale}")
    if assessment.prioritized_questions:
        lines.extend(["", "Priority questions:"])
        for index, question in enumerate(assessment.prioritized_questions, start=1):
            lines.append(f"{index}. {question}")
    lines.extend(["", "## Quality-control results", ""])
    lines.append(f"- Type 1 bins below 95% expected coverage: **{low_coverage_bins}**")
    lines.append(f"- Low-coverage bins containing same-cage events: **{low_coverage_event_bins}**")
    if aggregation is not None:
        lines.append(
            f"- Type 1/Type 2 cage-value agreement: {aggregation.matched_rows}/"
            f"{aggregation.compared_rows} rows within tolerance {aggregation.tolerance:g}"
        )
    if group_stats is not None:
        lines.append(
            f"- Vendor group average matches recomputed mean in {group_stats.average_matches}/"
            f"{group_stats.compared_rows} rows"
        )
        lines.append(
            f"- Vendor-labelled SEM matches conventional SEM in "
            f"{group_stats.sem_matches_conventional}/{group_stats.compared_rows} rows and sample SD "
            f"in {group_stats.sem_matches_sample_sd}/{group_stats.compared_rows} rows. This is an "
            "export-specific contract check, not a general conclusion about DVC Analytics."
        )
    lines.extend(["", "## Proposed agentic analysis plan", ""])
    for step in plan.steps:
        suffix = f" — blocked: {step.blocked_reason}" if step.blocked_reason else ""
        approval = " [approval required]" if step.approval_required else ""
        lines.append(f"1. **{step.title}** (`{step.tool_name}`) — {step.status}{approval}{suffix}")
    lines.extend(
        [
            "",
            "## Current limitations",
            "",
            "- The original study goal and biological meanings of Control/Donor-like labels are unknown.",
            "- Five animals per cage is a reported reference value, not yet a time-valid occupancy record.",
            "- Light/dark and Zeitgeber analyses remain blocked until REM or explicit photoperiod metadata are supplied.",
            "- Event origins and code semantics are not yet governed; no event-triggered exclusion is automatic.",
            "- Numerical science tools are intentionally delegated to UDWA rather than reimplemented by the agent.",
            "",
            "## Traceability",
            "",
            "The output directory contains the normalized tables, typed study context, JSON Schema, "
            "metadata assessment, guarded plan, validation results, evidence ledger and source hashes.",
        ]
    )
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> Path:
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    inspections = []

    type1, type1_inspection = normalize_type1(
        read_csv(args.type1),
        source_file=Path(args.type1).name,
        metric_name=args.metric,
        expected_frequency_hz=args.expected_frequency_hz,
    )
    type1.to_csv(output / "type1_normalized.csv", index=False)
    inspections.append(type1_inspection)

    type2 = None
    type2_inspection = None
    if args.type2:
        type2, type2_inspection = normalize_type2(
            read_csv(args.type2), source_file=Path(args.type2).name, metric_name=args.metric
        )
        type2.to_csv(output / "type2_normalized.csv", index=False)
        inspections.append(type2_inspection)

    events = None
    if args.events:
        events, event_inspection = normalize_events(
            read_csv(args.events), source_file=Path(args.events).name
        )
        events.to_csv(output / "events_normalized.csv", index=False)
        inspections.append(event_inspection)
        type1 = annotate_event_counts(type1, events)
        type1.to_csv(output / "type1_event_annotated.csv", index=False)

    detected_cages = sorted(type1["cage_id"].dropna().astype(str).unique().tolist())
    bundle = load_udwa_bundle(args.metadata) if args.metadata else {}
    context = context_from_udwa_bundle(
        bundle, study_id=args.study_id, detected_cages=detected_cages
    )
    context = apply_known_poc_context(context, animals_per_cage=args.animals_per_cage)
    context = add_acquisition_context(
        context,
        metric_name=args.metric,
        frequency_hz=args.expected_frequency_hz,
        export_bin_seconds=type1_inspection.native_bin_seconds,
    )
    context.environment.source_utc_offset = context.environment.source_utc_offset.recorded(
        "-04:00", "POC expert input"
    )
    for role, path in (
        ("type1", args.type1),
        ("type2", args.type2),
        ("events", args.events),
        ("metadata", args.metadata),
    ):
        if path:
            context = register_asset(context, path, role, file_sha256(path))

    assessment = assess_metadata(context)
    plan = propose_plan(context, assessment)
    low_coverage = type1["coverage_fraction"].lt(args.coverage_threshold).fillna(False)
    low_coverage_bins = int(low_coverage.sum())
    low_coverage_event_bins = int(
        (low_coverage & type1.get("event_count", pd.Series(0, index=type1.index)).gt(0)).sum()
    )
    plan = adapt_plan_for_event_qc(plan, low_coverage_event_bins)
    violations = validate_plan(plan, context)

    aggregation = validate_type1_against_type2(type1, type2) if type2 is not None else None
    group_stats = validate_type2_group_statistics(type2) if type2 is not None else None

    ledger = EvidenceLedger()
    context_ev = add_evidence(
        ledger,
        kind="metadata",
        source="study_context.json",
        description="Typed DVC scientific context",
        payload=context.model_dump(mode="json"),
    )
    qc_ev = add_evidence(
        ledger,
        kind="computation",
        source="DVC deterministic QC",
        description="Coverage and event-aware QC summary",
        payload={
            "low_coverage_bins": low_coverage_bins,
            "low_coverage_event_bins": low_coverage_event_bins,
        },
    )
    add_claim(
        ledger,
        text="The current fixture supports cage-level exploratory QC but not a governed biological group comparison.",
        kind="agent_inference",
        evidence_ids=[context_ev],
        limitations=["Original study objective and group meanings are unknown"],
    )
    if low_coverage_event_bins:
        add_decision(
            ledger,
            issue="Event-associated coverage loss",
            action="Run retain-versus-approved-mask sensitivity analysis",
            rationale="Separate acquisition/handling sensitivity from ordinary cage activity.",
            evidence_ids=[qc_ev],
        )

    _json(output / "study_context.json", context)
    _json(output / "study_context.schema.json", context.model_json_schema())
    _json(output / "metadata_assessment.json", assessment)
    _json(output / "analysis_plan.json", plan)
    _json(output / "plan_violations.json", violations)
    _json(
        output / "export_inspections.json", [item.model_dump(mode="json") for item in inspections]
    )
    _json(output / "evidence_ledger.json", ledger)
    if aggregation is not None:
        _json(output / "type1_type2_validation.json", aggregation)
    if group_stats is not None:
        _json(output / "type2_group_statistics_validation.json", group_stats)
    (output / "poc_report.md").write_text(
        _report(
            context=context,
            assessment=assessment,
            plan=plan,
            inspections=inspections,
            aggregation=aggregation,
            group_stats=group_stats,
            low_coverage_bins=low_coverage_bins,
            low_coverage_event_bins=low_coverage_event_bins,
        ),
        encoding="utf-8",
    )
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the metadata-aware Tecniplast DVC POC")
    parser.add_argument("--type1", required=True, help="DVC Analytics Type 1 CSV")
    parser.add_argument("--type2", help="DVC Analytics Type 2 CSV")
    parser.add_argument("--events", help="DVC event CSV")
    parser.add_argument("--metadata", help="UDWA metadata bundle JSON")
    parser.add_argument("--metric", default="animal_loc__index_smoothed")
    parser.add_argument("--study-id", default="dvc-poc")
    parser.add_argument("--animals-per-cage", type=int, default=5)
    parser.add_argument("--expected-frequency-hz", type=float, default=4.0)
    parser.add_argument("--coverage-threshold", type=float, default=0.95)
    parser.add_argument("--output", required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    output = run(args)
    print(f"DVC POC bundle written to {output}")


if __name__ == "__main__":
    main()
