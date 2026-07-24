"""Conservative UDWA/MNMS-DVC context construction and metadata assessment."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openscientist.dvc.models import (
    AcquisitionContext,
    CageContext,
    ContextValue,
    MetadataAssessment,
    MetadataGap,
    MetadataLevel,
    RequirementLevel,
    SourceAsset,
    StudyContext,
    UnitDefinition,
    ValueStatus,
)

EXPLORATORY_OBJECTIVE = (
    "Exploratory quality control and descriptive characterization of cage-level DVC activity; "
    "no biological group contrast or causal inference is prespecified."
)


def load_udwa_bundle(path: str | Path) -> dict[str, Any]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("UDWA metadata bundle must be a JSON object")
    return raw


def context_from_udwa_bundle(
    bundle: dict[str, Any], *, study_id: str, detected_cages: list[str]
) -> StudyContext:
    """Convert the flat UDWA bundle conservatively; labels never imply biology."""

    study = bundle.get("study") if isinstance(bundle.get("study"), dict) else {}
    subjects = bundle.get("subjects") if isinstance(bundle.get("subjects"), list) else []
    groups = bundle.get("groups") if isinstance(bundle.get("groups"), list) else []
    subject_by_cage = {
        str(row.get("cage_id") or row.get("subject_id")): row
        for row in subjects
        if isinstance(row, dict) and (row.get("cage_id") or row.get("subject_id"))
    }
    group_by_id = {
        str(row.get("group_id")): row
        for row in groups
        if isinstance(row, dict) and row.get("group_id")
    }
    cages: list[CageContext] = []
    for cage_id in detected_cages:
        row = subject_by_cage.get(cage_id, {})
        detected_group = row.get("group_id_detected")
        group_row = group_by_id.get(str(detected_group), {})
        explicit_biology = row.get("treatment_group") or group_row.get("experimental_condition")
        cages.append(
            CageContext(
                cage_id=cage_id,
                submitted_label=cage_id,
                export_group=(
                    ContextValue[str].recorded(str(detected_group), "UDWA subjects.group_id_detected")
                    if detected_group
                    else ContextValue[str].unknown()
                ),
                biological_group=(
                    ContextValue[str].recorded(str(explicit_biology), "UDWA explicit group metadata")
                    if explicit_biology
                    else ContextValue[str].unknown(
                        "Cage labels such as Control or Donor are opaque without explicit metadata"
                    )
                ),
            )
        )
    title = study.get("study_name") or study.get("project_name")
    objective = study.get("experiment_description")
    context = StudyContext(
        metadata_level=MetadataLevel.M1_UDWA,
        study_id=study_id,
        title=(ContextValue[str].recorded(str(title), "UDWA study metadata") if title else ContextValue[str].unknown()),
        objective=(
            ContextValue[str].recorded(str(objective), "UDWA study metadata")
            if objective
            else ContextValue[str].unknown()
        ),
        cages=cages,
    )
    timezone = study.get("timezone")
    light_on = study.get("light_on_time")
    light_off = study.get("light_off_time")
    if timezone:
        context.environment.iana_timezone = ContextValue[str].recorded(str(timezone), "UDWA study.timezone")
    if light_on:
        context.environment.light_on = ContextValue[str].recorded(str(light_on), "UDWA study.light_on_time")
    if light_off:
        context.environment.light_off = ContextValue[str].recorded(str(light_off), "UDWA study.light_off_time")
    return context


def apply_known_poc_context(context: StudyContext, *, animals_per_cage: int = 5) -> StudyContext:
    """Apply only facts supplied for the engineering fixture, not unknown study biology."""

    out = context.model_copy(deep=True)
    out.metadata_level = MetadataLevel.M2_MNMS_DVC
    out.objective = ContextValue[str].recorded(EXPLORATORY_OBJECTIVE, "POC design decision", approved=True)
    out.mode = "exploratory"
    out.units = UnitDefinition(
        experimental_unit=ContextValue[str].recorded("cage", "POC expert input", approved=True),
        observation_unit=ContextValue[str].recorded(
            "cage × time interval × metric", "POC scientific contract", approved=True
        ),
        analysis_unit=ContextValue[str].recorded(
            "cage or cage episode", "POC scientific contract", approved=True
        ),
    )
    for cage in out.cages:
        cage.animals_per_cage = ContextValue[int].recorded(
            animals_per_cage,
            "POC expert input",
            note="Reference count; longitudinal constancy requires occupancy-event validation",
        )
    out.unresolved_notes.extend(
        [
            "Original study objective is unknown.",
            "Biological meanings of cage labels and groups are unknown.",
            "Five animals per cage is reported but time-valid occupancy is not yet reconstructed.",
        ]
    )
    return out


def add_acquisition_context(
    context: StudyContext,
    *,
    metric_name: str,
    frequency_hz: float | None,
    export_bin_seconds: float | None,
) -> StudyContext:
    out = context.model_copy(deep=True)
    out.acquisition = AcquisitionContext(
        metric_name=ContextValue[str].recorded(metric_name, "file name / CLI parameter"),
        native_frequency_hz=(
            ContextValue[float].recorded(frequency_hz, "DVC technical context")
            if frequency_hz is not None
            else ContextValue[float].unknown()
        ),
        export_bin_seconds=(
            ContextValue[float].computed(export_bin_seconds, "timestamp interval inspection")
            if export_bin_seconds is not None
            else ContextValue[float].unknown()
        ),
    )
    return out


def register_asset(context: StudyContext, path: str | Path, role: str, sha256: str) -> StudyContext:
    out = context.model_copy(deep=True)
    out.source_assets.append(
        SourceAsset(
            asset_id=f"asset-{len(out.source_assets) + 1:03d}",
            filename=Path(path).name,
            role=role,  # type: ignore[arg-type]
            sha256=sha256,
        )
    )
    return out


def ablate_context(context: StudyContext, ablation_id: str) -> StudyContext:
    """Create deterministic metadata-ablation conditions from one complete record."""

    out = context.model_copy(deep=True)
    if ablation_id == "A1_analysis_intent":
        out.objective = ContextValue[str].unknown("Removed for metadata-ablation experiment")
    elif ablation_id == "A2_unit_of_inference":
        out.units = UnitDefinition()
    elif ablation_id == "A3_occupancy":
        for cage in out.cages:
            cage.animals_per_cage = ContextValue[int].unknown("Removed for metadata-ablation experiment")
    elif ablation_id == "A4_light_rem":
        out.environment.light_on = ContextValue[str].unknown()
        out.environment.light_off = ContextValue[str].unknown()
        out.environment.rem_source = ContextValue[str].unknown()
    elif ablation_id == "A5_event_semantics":
        out.events.event_code_definitions = ContextValue[dict[str, str]].unknown()
        out.events.exclusion_policy = ContextValue[str].unknown()
    elif ablation_id == "A6_acquisition_provenance":
        out.acquisition.metric_definition = ContextValue[str].unknown()
        out.acquisition.native_frequency_hz = ContextValue[float].unknown()
        out.acquisition.export_bin_seconds = ContextValue[float].unknown()
    else:
        raise ValueError(f"unknown ablation: {ablation_id}")
    return out


def _unknown(value: ContextValue[Any]) -> bool:
    return value.status is ValueStatus.UNKNOWN


def assess_metadata(context: StudyContext, *, max_questions: int = 5) -> MetadataAssessment:
    gaps: list[MetadataGap] = []

    def add(
        gap_id: str,
        field: str,
        level: RequirementLevel,
        rationale: str,
        question: str,
        priority: int,
        blocks: list[str],
    ) -> None:
        gaps.append(
            MetadataGap(
                gap_id=gap_id,
                field_path=field,
                level=level,
                rationale=rationale,
                question=question,
                priority=priority,
                blocks=blocks,
            )
        )

    if _unknown(context.objective):
        add(
            "intent",
            "objective",
            RequirementLevel.BLOCKING,
            "Analysis choice depends on the scientific or operational purpose.",
            "What decision or scientific question should this analysis support?",
            100,
            ["analysis plan"],
        )
    if _unknown(context.units.experimental_unit):
        add(
            "experimental-unit",
            "units.experimental_unit",
            RequirementLevel.BLOCKING,
            "An unknown experimental unit can create pseudoreplication.",
            "What is the experimental unit: cage, animal, rack, room, or cohort?",
            98,
            ["inferential statistics", "group comparison"],
        )
    if any(_unknown(cage.animals_per_cage) for cage in context.cages):
        add(
            "occupancy",
            "cages[].animals_per_cage",
            RequirementLevel.ANALYSIS_DEPENDENT,
            "Cage-level signal cannot be normalized per animal without time-valid occupancy.",
            "How many animals occupied each cage, and did occupancy change?",
            90,
            ["per-animal normalization", "occupancy-dependent interpretation"],
        )
    if _unknown(context.environment.light_on) or _unknown(context.environment.light_off):
        add(
            "photoperiod",
            "environment.light_on/light_off",
            RequirementLevel.ANALYSIS_DEPENDENT,
            "Zeitgeber and light/dark analyses require the facility light schedule.",
            "What were light-on and light-off times, or which REM record supplies them?",
            88,
            ["light/dark analysis", "ZT interpretation"],
        )
    if _unknown(context.events.event_code_definitions):
        add(
            "event-semantics",
            "events.event_code_definitions",
            RequirementLevel.ANALYSIS_DEPENDENT,
            "Event labels cannot safely determine occupancy or exclusion policy by name alone.",
            "What do the exported event codes mean, and which are system versus manual?",
            86,
            ["automatic exclusions", "occupancy reconstruction"],
        )
    if _unknown(context.acquisition.metric_definition):
        add(
            "metric-definition",
            "acquisition.metric_definition",
            RequirementLevel.RECOMMENDED,
            "A numeric index is not biologically interpretable without its operational definition.",
            "What exact DVC metric and calculation definition produced these values?",
            78,
            ["biological interpretation"],
        )
    if any(_unknown(cage.biological_group) for cage in context.cages):
        add(
            "group-semantics",
            "cages[].biological_group",
            RequirementLevel.ANALYSIS_DEPENDENT,
            "Labels that resemble Control or Donor are not evidence of assignment.",
            "Do cage labels encode biological groups, and what are their governed meanings?",
            92,
            ["biological group comparison", "causal claims"],
        )

    gaps.sort(key=lambda item: (-item.priority, item.gap_id))
    blocked = sorted({analysis for gap in gaps for analysis in gap.blocks})
    blocking = any(gap.level is RequirementLevel.BLOCKING for gap in gaps)
    weighted_penalty = sum(
        {
            RequirementLevel.BLOCKING: 20,
            RequirementLevel.ANALYSIS_DEPENDENT: 10,
            RequirementLevel.RECOMMENDED: 5,
            RequirementLevel.DESCRIPTIVE: 2,
        }[gap.level]
        for gap in gaps
    )
    return MetadataAssessment(
        gaps=gaps,
        ready_for_qc=True,
        ready_for_descriptive_analysis=not blocking,
        blocked_analyses=blocked,
        prioritized_questions=[gap.question for gap in gaps[:max_questions]],
        quality_score=max(0.0, 100.0 - weighted_penalty),
    )
