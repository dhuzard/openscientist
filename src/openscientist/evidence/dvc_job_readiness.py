#!/usr/bin/env python3
"""Fail-closed readiness audit for exporting an OpenScientist DVC job as HCMO evidence.

The audit is intentionally read-only. It inventories immutable job artifacts and
reports whether enough governed metadata exists to run the strict HCMO evidence
export. It does not infer light schedules, cage identities, housing assignments,
or finding provenance from narrative text.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
import re
import statistics
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import asyncpg  # type: ignore[import-untyped]

STATUSES = {"PASS", "WARN", "FAIL", "UNAVAILABLE"}
SUMMARY_SUFFIXES = ("_TIMESTAMP", "_AVG", "_SEM", "_QRT", "_SAMPLES")
ACTIVITY_PATTERN = "*_animal_loc__index_smoothed.csv"
EVENT_PATTERN = "*_events.csv"


class ReadinessAuditError(ValueError):
    """The job directory cannot be safely audited."""


@dataclass(frozen=True)
class Check:
    check: str
    family: str
    status: str
    reason_code: str
    detail: str
    observed: Any = None
    threshold: Any = None

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"invalid check status: {self.status}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_timestamp(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def _source_offset(value: str) -> str | None:
    match = re.search(r"([+-]\d{2}:?\d{2}|Z)$", value.strip())
    return match.group(1) if match else None


def _cohort_from_name(path: Path, suffix: str) -> str:
    return path.name.removesuffix(suffix)


def _activity_file_inventory(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames or []
        timestamp_fields = [field for field in fields if field.endswith("_TIMESTAMP")]
        if not timestamp_fields:
            raise ReadinessAuditError(f"no *_TIMESTAMP field in {path.name}")

        groups: list[dict[str, Any]] = []
        group_state: dict[str, dict[str, Any]] = {}
        for timestamp_field in timestamp_fields:
            prefix = timestamp_field.removesuffix("_TIMESTAMP")
            summaries = {f"{prefix}{suffix}" for suffix in SUMMARY_SUFFIXES}
            traces = [
                field
                for field in fields
                if field.startswith(f"{prefix}_") and field not in summaries
            ]
            if not traces:
                raise ReadinessAuditError(
                    f"timestamp group {timestamp_field} has no physical traces in {path.name}"
                )
            group_state[timestamp_field] = {
                "prefix": prefix,
                "trace_fields": traces,
                "nonempty_timestamps": 0,
                "invalid_timestamps": 0,
                "missing_offsets": 0,
                "offsets": set(),
                "previous": None,
                "interval_seconds": [],
            }

        row_count = 0
        for row in reader:
            row_count += 1
            for timestamp_field, state in group_state.items():
                raw = (row.get(timestamp_field) or "").strip()
                if not raw:
                    continue
                state["nonempty_timestamps"] += 1
                offset = _source_offset(raw)
                if offset is None:
                    state["missing_offsets"] += 1
                else:
                    state["offsets"].add(offset)
                try:
                    parsed = _parse_timestamp(raw)
                except ValueError:
                    state["invalid_timestamps"] += 1
                    continue
                previous = state["previous"]
                if previous is not None:
                    delta = (parsed - previous).total_seconds()
                    if delta > 0:
                        state["interval_seconds"].append(delta)
                state["previous"] = parsed

    for timestamp_field, state in group_state.items():
        intervals = state.pop("interval_seconds")
        state.pop("previous")
        positive_intervals = Counter(int(value) for value in intervals)
        groups.append(
            {
                "timestamp_field": timestamp_field,
                "group": state["prefix"],
                "trace_fields": state["trace_fields"],
                "trace_count": len(state["trace_fields"]),
                "nonempty_timestamps": state["nonempty_timestamps"],
                "invalid_timestamps": state["invalid_timestamps"],
                "missing_offsets": state["missing_offsets"],
                "source_utc_offsets": sorted(state["offsets"]),
                "median_native_interval_seconds": (
                    statistics.median(intervals) if intervals else None
                ),
                "native_interval_counts": dict(sorted(positive_intervals.items())),
            }
        )

    return {
        "path": f"data/{path.name}",
        "filename": path.name,
        "cohort": _cohort_from_name(path, "_animal_loc__index_smoothed.csv"),
        "bytes": path.stat().st_size,
        "sha256": _sha256_file(path),
        "rows": row_count,
        "columns": len(fields),
        "groups": groups,
        "trace_count": sum(group["trace_count"] for group in groups),
    }


def _event_file_inventory(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)
        try:
            header = next(reader)
        except StopIteration:
            header = []
        rows = sum(1 for _ in reader)
    return {
        "path": f"data/{path.name}",
        "filename": path.name,
        "cohort": _cohort_from_name(path, "_events.csv"),
        "bytes": path.stat().st_size,
        "sha256": _sha256_file(path),
        "rows": rows,
        "columns": len(header),
        "header": header,
    }


def _check(
    name: str,
    family: str,
    status: str,
    reason_code: str,
    detail: str,
    *,
    observed: Any = None,
    threshold: Any = None,
) -> Check:
    return Check(name, family, status, reason_code, detail, observed, threshold)


def _governance_checks(
    governance_manifest: dict[str, Any] | None,
    activity_files: list[dict[str, Any]],
    *,
    inferred_schedule: bool,
) -> list[Check]:
    """Validate explicit cage-level authority without inferring missing facts."""
    observed_mappings = {
        (item["filename"], group["timestamp_field"], trace_field)
        for item in activity_files
        for group in item["groups"]
        for trace_field in group["trace_fields"]
    }
    if governance_manifest is None:
        return [
            _check(
                "expected_cage_reconciliation",
                "cage_identity",
                "UNAVAILABLE",
                "expected_cage_table_missing",
                "Trace columns were enumerated, but no independent expected-cage table was supplied.",
                observed=len(observed_mappings),
            ),
            _check(
                "physical_cage_identity",
                "cage_identity",
                "UNAVAILABLE",
                "validated_cage_mapping_missing",
                "Trace labels cannot be assumed to be globally unique physical-cage identifiers without governed mapping metadata.",
            ),
            _check(
                "per_cage_light_schedule",
                "biological_time",
                "UNAVAILABLE",
                "governed_light_schedule_missing",
                "The report describes the light/dark boundary as inferred; inferred clock windows cannot become governed schedule facts."
                if inferred_schedule
                else "No governed per-cage light schedule was found in job artifacts.",
            ),
            _check(
                "per_cage_timezone",
                "biological_time",
                "UNAVAILABLE",
                "timezone_authority_missing",
                "UTC offsets are preserved, but an offset alone is not an attributable per-cage timezone/DST policy.",
            ),
            _check(
                "housing_assignment",
                "hcmo_context",
                "UNAVAILABLE",
                "housing_assignment_missing",
                "No validated subject-to-physical-cage housing assignment was found.",
            ),
            _check(
                "enclosure_dimensions",
                "hcmo_context",
                "UNAVAILABLE",
                "enclosure_dimensions_missing",
                "No governed physical enclosure dimensions were found; the strict HCMO shape requires them.",
            ),
        ]

    authority = governance_manifest.get("authority")
    authority_approved = bool(
        isinstance(authority, dict)
        and authority.get("id")
        and authority.get("approved_at")
        and authority.get("review_state") == "approved"
    )
    cages_value = governance_manifest.get("cages")
    cages = cages_value if isinstance(cages_value, list) else []
    cage_records = [item for item in cages if isinstance(item, dict)]
    expected_count = governance_manifest.get("expected_cage_count")
    mapping_fields = ("cage_id", "source_file", "timestamp_field", "trace_field")
    mapping_fields_present = len(cage_records) == len(cages) and all(
        all(item.get(field) not in (None, "") for field in mapping_fields)
        for item in cage_records
    )
    expected_mappings = {
        (
            str(item.get("source_file")),
            str(item.get("timestamp_field")),
            str(item.get("trace_field")),
        )
        for item in cage_records
    }
    cage_ids = [str(item.get("cage_id")) for item in cage_records if item.get("cage_id")]
    reconciled = bool(
        authority_approved
        and mapping_fields_present
        and isinstance(expected_count, int)
        and expected_count == len(cage_records) == len(observed_mappings)
        and expected_mappings == observed_mappings
    )
    unique_cages = reconciled and len(cage_ids) == len(set(cage_ids))

    schedule_valid = authority_approved and bool(cage_records)
    for cage in cage_records:
        try:
            lights_on = datetime.strptime(str(cage["lights_on"]), "%H:%M:%S").time()
            lights_off = datetime.strptime(str(cage["lights_off"]), "%H:%M:%S").time()
            schedule_valid = schedule_valid and lights_on != lights_off
        except (KeyError, TypeError, ValueError):
            schedule_valid = False
        schedule_valid = schedule_valid and bool(cage.get("schedule_authority")) and bool(
            cage.get("transition_policy")
        )

    observed_offsets = {
        (item["filename"], group["timestamp_field"]): set(group["source_utc_offsets"])
        for item in activity_files
        for group in item["groups"]
    }
    timezone_valid = authority_approved and bool(cage_records)
    for cage in cage_records:
        try:
            ZoneInfo(str(cage["timezone"]))
        except (KeyError, ZoneInfoNotFoundError):
            timezone_valid = False
        declared_offsets = cage.get("source_utc_offsets")
        key = (str(cage.get("source_file")), str(cage.get("timestamp_field")))
        timezone_valid = timezone_valid and isinstance(declared_offsets, list) and bool(
            declared_offsets
        )
        if isinstance(declared_offsets, list):
            timezone_valid = timezone_valid and observed_offsets.get(key, set()).issubset(
                set(map(str, declared_offsets))
            )

    housing_valid = authority_approved and bool(cage_records)
    subject_ids: list[str] = []
    for cage in cage_records:
        subjects = cage.get("subjects")
        if not isinstance(subjects, list) or not subjects:
            housing_valid = False
            continue
        for subject in subjects:
            if not isinstance(subject, dict) or not subject.get("id"):
                housing_valid = False
                continue
            subject_ids.append(str(subject["id"]))
            try:
                start = _parse_timestamp(str(subject["housing_start"]))
                end = _parse_timestamp(str(subject["housing_end"]))
                housing_valid = housing_valid and start < end
            except (KeyError, TypeError, ValueError):
                housing_valid = False
    housing_valid = housing_valid and len(subject_ids) == len(set(subject_ids))

    dimensions_valid = authority_approved and bool(cage_records)
    enclosure_ids: list[str] = []
    for cage in cage_records:
        enclosure = cage.get("enclosure")
        if not isinstance(enclosure, dict):
            dimensions_valid = False
            continue
        if enclosure.get("id"):
            enclosure_ids.append(str(enclosure["id"]))
        try:
            dimensions_valid = dimensions_valid and all(
                float(enclosure[axis]) > 0 for axis in ("width", "length", "height")
            )
        except (KeyError, TypeError, ValueError):
            dimensions_valid = False
        dimensions_valid = dimensions_valid and bool(enclosure.get("dimension_unit_iri"))
    dimensions_valid = dimensions_valid and len(enclosure_ids) == len(set(enclosure_ids))

    authority_reason = (
        "governed_manifest_approved" if authority_approved else "governance_manifest_unapproved"
    )
    return [
        _check(
            "expected_cage_reconciliation",
            "cage_identity",
            "PASS" if reconciled else "FAIL",
            "expected_cages_reconciled" if reconciled else "expected_cage_mismatch",
            "The approved manifest exactly reconciles every source trace to one expected cage."
            if reconciled
            else "The governed expected-cage set does not exactly match the observed source traces.",
            observed={"manifest": len(cage_records), "source_traces": len(observed_mappings)},
            threshold={"exact_match": True},
        ),
        _check(
            "physical_cage_identity",
            "cage_identity",
            "PASS" if unique_cages else "FAIL",
            "physical_cage_mappings_unique" if unique_cages else "physical_cage_mapping_invalid",
            "Every observed trace maps to one unique governed physical-cage identifier."
            if unique_cages
            else "Physical-cage identifiers are missing, duplicated, unapproved, or not exactly reconciled.",
        ),
        _check(
            "per_cage_light_schedule",
            "biological_time",
            "PASS" if schedule_valid else "FAIL",
            "per_cage_schedule_governed" if schedule_valid else "per_cage_schedule_invalid",
            "Every cage has an approved lights-on/off schedule, transition policy, and authority."
            if schedule_valid
            else "At least one cage lacks a valid approved light schedule or schedule authority.",
        ),
        _check(
            "per_cage_timezone",
            "biological_time",
            "PASS" if timezone_valid else "FAIL",
            "per_cage_timezone_governed" if timezone_valid else "per_cage_timezone_invalid",
            "Every cage has an attributable IANA timezone and declared offsets covering its source timestamps."
            if timezone_valid
            else "At least one cage lacks a valid attributable timezone or source-offset declaration.",
        ),
        _check(
            "housing_assignment",
            "hcmo_context",
            "PASS" if housing_valid else "FAIL",
            "housing_assignments_governed" if housing_valid else "housing_assignment_invalid",
            "Every cage has one or more uniquely identified subjects with bounded housing intervals."
            if housing_valid
            else "Subject identities or bounded housing assignments are missing, duplicated, or invalid.",
        ),
        _check(
            "enclosure_dimensions",
            "hcmo_context",
            "PASS" if dimensions_valid else "FAIL",
            "enclosure_dimensions_governed" if dimensions_valid else "enclosure_dimensions_invalid",
            "Every uniquely identified enclosure has positive governed dimensions and a unit IRI."
            if dimensions_valid
            else "Enclosure identities, dimensions, or units are missing, duplicated, or invalid.",
            observed={"authority": authority_reason},
        ),
    ]


async def load_relational_inventory(job_id: str, database_url: str) -> dict[str, Any]:
    """Read only the structural evidence counts needed by the readiness audit."""
    connect_url = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    connection = await asyncpg.connect(connect_url)
    try:
        row = await connection.fetchrow(
            """
            SELECT
              j.id::text AS job_id,
              j.status,
              j.current_iteration,
              (SELECT count(*) FROM hypotheses h WHERE h.job_id = j.id) AS hypotheses,
              (SELECT count(*) FROM findings f WHERE f.job_id = j.id) AS findings,
              (SELECT count(*) FROM analysis_log a WHERE a.job_id = j.id) AS analyses,
              (SELECT count(*) FROM literature l WHERE l.job_id = j.id) AS literature,
              (SELECT count(*) FROM job_data_files d WHERE d.job_id = j.id) AS data_files,
              (SELECT count(*) FROM finding_hypotheses fh
                 JOIN findings f ON f.id = fh.finding_id WHERE f.job_id = j.id)
                 AS finding_hypothesis_links,
              (SELECT count(*) FROM finding_literature fl
                 JOIN findings f ON f.id = fl.finding_id WHERE f.job_id = j.id)
                 AS finding_literature_links,
              to_regclass('public.statistical_results') IS NOT NULL
                 AS statistical_results_table,
              to_regclass('public.finding_analyses') IS NOT NULL AS finding_analyses_table,
              to_regclass('public.finding_data_files') IS NOT NULL AS finding_data_files_table
            FROM jobs j
            WHERE j.id = $1::uuid
            """,
            job_id,
        )
        if row is None:
            raise ReadinessAuditError(f"job not found in authoritative database: {job_id}")
        return dict(row)
    finally:
        await connection.close()


def audit_dvc_job(
    job_dir: Path,
    *,
    relational_inventory: dict[str, Any] | None = None,
    governance_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Audit a completed filesystem job without modifying it."""
    job_dir = job_dir.resolve()
    data_dir = job_dir / "data"
    report_path = job_dir / "final_report.md"
    if not job_dir.is_dir():
        raise ReadinessAuditError(f"job directory does not exist: {job_dir}")
    if not data_dir.is_dir():
        raise ReadinessAuditError(f"job data directory does not exist: {data_dir}")

    activity_paths = sorted(data_dir.glob(ACTIVITY_PATTERN))
    event_paths = sorted(data_dir.glob(EVENT_PATTERN))
    if not activity_paths:
        raise ReadinessAuditError("no DVC activity exports found")

    activity_files = [_activity_file_inventory(path) for path in activity_paths]
    event_files = [_event_file_inventory(path) for path in event_paths]
    groups = [group for item in activity_files for group in item["groups"]]
    observed_trace_count = sum(item["trace_count"] for item in activity_files)
    invalid_timestamps = sum(group["invalid_timestamps"] for group in groups)
    nonempty_timestamps = sum(group["nonempty_timestamps"] for group in groups)
    missing_offsets = sum(group["missing_offsets"] for group in groups)
    intervals = sorted(
        {
            int(group["median_native_interval_seconds"])
            for group in groups
            if group["median_native_interval_seconds"] is not None
        }
    )
    report = report_path.read_text(encoding="utf-8") if report_path.is_file() else ""
    inferred_schedule = bool(
        re.search(r"(?is)light.{0,80}(?:schedule|timing|phase|window).{0,80}infer", report)
        or re.search(r"(?is)infer.{0,80}light.{0,80}(?:schedule|timing|phase|window)", report)
        or re.search(r"(?is)(?:lights?-on|lights?-off).{0,100}infer", report)
    )

    activity_cohorts = {item["cohort"] for item in activity_files}
    event_cohorts = {item["cohort"] for item in event_files}
    checks = [
        _check(
            "source_files_hashed",
            "input_integrity",
            "PASS",
            "source_hashes_recorded",
            "Every discovered DVC activity/event source was hashed without modifying it.",
            observed=len(activity_files) + len(event_files),
        ),
        _check(
            "activity_schema_parsed",
            "input_integrity",
            "PASS",
            "schema_groups_enumerated",
            "Every activity export had one or more named timestamp groups and physical traces.",
            observed=len(groups),
        ),
        _check(
            "summary_columns_excluded",
            "cage_identity",
            "PASS",
            "summary_suffixes_excluded",
            "AVG, SEM, QRT, SAMPLES, and TIMESTAMP columns were excluded from trace counts.",
            observed=observed_trace_count,
        ),
        _check(
            "timestamp_parse_integrity",
            "time_integrity",
            "PASS" if invalid_timestamps == 0 else "FAIL",
            "timestamps_parse" if invalid_timestamps == 0 else "timestamp_parse_failure",
            "All non-empty timestamp values were parsed."
            if invalid_timestamps == 0
            else "One or more non-empty timestamp values could not be parsed.",
            observed={
                "parsed": nonempty_timestamps - invalid_timestamps,
                "invalid": invalid_timestamps,
            },
            threshold={"maximum_invalid": 0},
        ),
        _check(
            "source_utc_offsets_preserved",
            "time_integrity",
            "PASS" if missing_offsets == 0 else "FAIL",
            "source_offsets_present" if missing_offsets == 0 else "source_offset_missing",
            "Every non-empty source timestamp carries an explicit UTC offset."
            if missing_offsets == 0
            else "At least one source timestamp lacks an explicit UTC offset.",
            observed={
                "with_offset": nonempty_timestamps - missing_offsets,
                "without_offset": missing_offsets,
            },
            threshold={"maximum_without_offset": 0},
        ),
        _check(
            "event_file_pairing",
            "input_integrity",
            "PASS" if activity_cohorts == event_cohorts else "FAIL",
            "cohorts_reconciled" if activity_cohorts == event_cohorts else "cohort_event_mismatch",
            "Every activity cohort has one matching event export."
            if activity_cohorts == event_cohorts
            else "Activity and event cohort names do not reconcile.",
            observed={"activity": sorted(activity_cohorts), "events": sorted(event_cohorts)},
        ),
        _check(
            "native_sampling_intervals",
            "preprocessing",
            "WARN" if len(intervals) > 1 else "PASS",
            "mixed_native_intervals" if len(intervals) > 1 else "single_native_interval",
            "Mixed native intervals require cage-first common-grid aggregation before scientific comparison."
            if len(intervals) > 1
            else "One median native interval was observed.",
            observed=intervals,
        ),
    ]
    checks.extend(
        _governance_checks(
            governance_manifest, activity_files, inferred_schedule=inferred_schedule
        )
    )
    if relational_inventory is None:
        checks.extend(
            [
                _check(
                    "authoritative_job_state",
                    "provenance",
                    "UNAVAILABLE",
                    "relational_projection_not_supplied",
                    "No read-only inventory from authoritative PostgreSQL state was supplied.",
                ),
                _check(
                    "authoritative_evidence_links",
                    "provenance",
                    "UNAVAILABLE",
                    "relational_projection_not_supplied",
                    "Filesystem artifacts do not expose exact finding-to-analysis/data/result identifiers.",
                ),
                _check(
                    "first_class_statistical_results",
                    "statistical_semantics",
                    "UNAVAILABLE",
                    "relational_projection_not_supplied",
                    "Statistical-result schema availability was not checked against PostgreSQL.",
                ),
            ]
        )
    else:
        if str(relational_inventory.get("job_id")) != job_dir.name:
            raise ReadinessAuditError("relational inventory job ID does not match job directory")
        finding_count = int(relational_inventory["findings"])
        hypothesis_count = int(relational_inventory["hypotheses"])
        finding_hypothesis_links = int(relational_inventory["finding_hypothesis_links"])
        finding_literature_links = int(relational_inventory["finding_literature_links"])
        checks.extend(
            [
                _check(
                    "authoritative_job_state",
                    "provenance",
                    "PASS",
                    "relational_projection_loaded",
                    "The job and structural evidence counts were loaded from the supplied relational projection.",
                    observed={
                        key: relational_inventory[key]
                        for key in (
                            "status",
                            "hypotheses",
                            "findings",
                            "analyses",
                            "literature",
                            "data_files",
                        )
                    },
                ),
                _check(
                    "hypothesis_coverage",
                    "provenance",
                    "PASS"
                    if hypothesis_count > 0 and finding_hypothesis_links >= finding_count
                    else "UNAVAILABLE",
                    "finding_hypothesis_links_present"
                    if hypothesis_count > 0 and finding_hypothesis_links >= finding_count
                    else "finding_hypothesis_links_missing",
                    "Every finding has an authoritative hypothesis link."
                    if hypothesis_count > 0 and finding_hypothesis_links >= finding_count
                    else "The strict prototype requires hypothesis links, but this job has none for its findings.",
                    observed={
                        "hypotheses": hypothesis_count,
                        "findings": finding_count,
                        "links": finding_hypothesis_links,
                    },
                ),
                _check(
                    "literature_linkage",
                    "literature",
                    "PASS" if finding_literature_links >= finding_count else "UNAVAILABLE",
                    "finding_literature_links_present"
                    if finding_literature_links >= finding_count
                    else "finding_literature_links_missing",
                    "Every finding has an authoritative literature link."
                    if finding_literature_links >= finding_count
                    else "Citation text exists in finding JSON, but authoritative finding-literature links are absent.",
                    observed={"findings": finding_count, "links": finding_literature_links},
                ),
                _check(
                    "authoritative_evidence_links",
                    "provenance",
                    "PASS"
                    if relational_inventory["finding_analyses_table"]
                    and relational_inventory["finding_data_files_table"]
                    else "UNAVAILABLE",
                    "evidence_link_schema_present"
                    if relational_inventory["finding_analyses_table"]
                    and relational_inventory["finding_data_files_table"]
                    else "finding_analysis_data_link_schema_missing",
                    "Finding-to-analysis and finding-to-data link tables are present."
                    if relational_inventory["finding_analyses_table"]
                    and relational_inventory["finding_data_files_table"]
                    else "PostgreSQL does not provide first-class finding-to-analysis and finding-to-data links required by the evidence graph.",
                ),
                _check(
                    "first_class_statistical_results",
                    "statistical_semantics",
                    "PASS" if relational_inventory["statistical_results_table"] else "UNAVAILABLE",
                    "statistical_results_table_present"
                    if relational_inventory["statistical_results_table"]
                    else "statistical_result_entities_missing",
                    "A first-class statistical-results table is available."
                    if relational_inventory["statistical_results_table"]
                    else "Statistics are embedded in narrative finding evidence rather than authoritative STATO-ready result entities.",
                ),
            ]
        )
    serialized_checks = [asdict(item) for item in checks]
    counts = Counter(item.status for item in checks)
    eligible = all(item.status in {"PASS", "WARN"} for item in checks)
    return {
        "analysis": "OpenScientist DVC to HCMO evidence readiness",
        "job_id": job_dir.name,
        "mode": (
            "deterministic-read-only-filesystem-audit-with-governed-manifest"
            if governance_manifest is not None
            else "deterministic-read-only-filesystem-audit"
        ),
        "eligible_for_strict_hcmo_export": eligible,
        "summary": {
            "checks": len(checks),
            "pass": counts["PASS"],
            "warn": counts["WARN"],
            "fail": counts["FAIL"],
            "unavailable": counts["UNAVAILABLE"],
            "activity_files": len(activity_files),
            "event_files": len(event_files),
            "timestamp_groups": len(groups),
            "observed_trace_columns": observed_trace_count,
            "native_interval_seconds": intervals,
        },
        "input_manifest": activity_files + event_files,
        "relational_inventory": relational_inventory,
        "governance_manifest": governance_manifest,
        "checks": serialized_checks,
        "limitations": [
            "This preflight does not re-run or endorse the original statistical analysis.",
            "Observed trace-column count is not independently reconciled unless an approved governed manifest is supplied.",
            "PASS means a check executed and met its criterion; UNAVAILABLE blocks dependent evidence products.",
        ],
    }


def make_readiness_report(audit: dict[str, Any]) -> str:
    summary = audit["summary"]
    status = "ELIGIBLE" if audit["eligible_for_strict_hcmo_export"] else "BLOCKED"
    if audit["eligible_for_strict_hcmo_export"]:
        interpretation = (
            "Every required check executed using the supplied governed metadata and "
            "authoritative relational projection. Strict HCMO + PROV/STATO export may "
            "proceed for this snapshot. Eligibility does not certify the scientific "
            "analysis or turn the mixed-cadence warning into a pass."
        )
    else:
        interpretation = (
            "The source bytes and timestamp schemas can be audited, but strict HCMO + "
            "PROV/STATO export remains blocked until every required `UNAVAILABLE` item has "
            "authoritative data. In particular, narrative values and inferred light windows "
            "must not be promoted into canonical evidence entities."
        )
    lines = [
        "# HCMO evidence-export readiness",
        "",
        f"- Job: `{audit['job_id']}`",
        f"- Strict export: **{status}**",
        f"- Checks: {summary['pass']} PASS, {summary['warn']} WARN, "
        f"{summary['fail']} FAIL, {summary['unavailable']} UNAVAILABLE",
        f"- Sources: {summary['activity_files']} activity files and "
        f"{summary['event_files']} event files",
        f"- Enumerated traces: {summary['observed_trace_columns']} across "
        f"{summary['timestamp_groups']} timestamp groups",
        f"- Native median intervals: {summary['native_interval_seconds']} seconds",
        "",
        "This is a read-only readiness audit. It does not certify the original analysis or "
        "convert inferred metadata into facts.",
        "",
        "## Acceptance checks",
        "",
        "| Check | Family | Status | Reason | Detail |",
        "| --- | --- | --- | --- | --- |",
    ]
    for check in audit["checks"]:
        detail = str(check["detail"]).replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {check['check']} | {check['family']} | **{check['status']}** | "
            f"{check['reason_code']} | {detail} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            interpretation,
            "",
        ]
    )
    return "\n".join(lines)


def write_audit(audit: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "readiness.json").write_text(
        json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    (output_dir / "readiness.md").write_text(
        make_readiness_report(audit), encoding="utf-8", newline="\n"
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--database-url-env",
        help="Optional environment-variable name containing a read-only PostgreSQL URL.",
    )
    parser.add_argument(
        "--relational-inventory-env",
        help="Optional environment-variable name containing a pre-queried JSON inventory.",
    )
    parser.add_argument(
        "--relational-inventory",
        type=Path,
        help="Optional path to a non-secret pre-queried JSON inventory.",
    )
    parser.add_argument(
        "--governance-manifest",
        type=Path,
        help="Optional approved cage/schedule/housing manifest to validate exactly.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        relational_inventory = None
        inventory_sources = sum(
            bool(value)
            for value in (
                args.database_url_env,
                args.relational_inventory_env,
                args.relational_inventory,
            )
        )
        if inventory_sources > 1:
            raise ReadinessAuditError(
                "choose one relational inventory source"
            )
        if args.database_url_env:
            database_url = os.environ.get(args.database_url_env)
            if not database_url:
                raise ReadinessAuditError(
                    f"database URL environment variable is unset: {args.database_url_env}"
                )
            relational_inventory = asyncio.run(
                load_relational_inventory(args.job_dir.resolve().name, database_url)
            )
        elif args.relational_inventory_env:
            inventory_text = os.environ.get(args.relational_inventory_env)
            if not inventory_text:
                raise ReadinessAuditError(
                    "relational inventory environment variable is unset: "
                    f"{args.relational_inventory_env}"
                )
            relational_inventory = json.loads(inventory_text)
            if not isinstance(relational_inventory, dict):
                raise ReadinessAuditError("relational inventory JSON must be an object")
        elif args.relational_inventory:
            relational_inventory = json.loads(
                args.relational_inventory.read_text(encoding="utf-8")
            )
            if not isinstance(relational_inventory, dict):
                raise ReadinessAuditError("relational inventory JSON must be an object")
        governance_manifest = None
        if args.governance_manifest:
            governance_manifest = json.loads(
                args.governance_manifest.read_text(encoding="utf-8")
            )
            if not isinstance(governance_manifest, dict):
                raise ReadinessAuditError("governance manifest JSON must be an object")
        audit = audit_dvc_job(
            args.job_dir,
            relational_inventory=relational_inventory,
            governance_manifest=governance_manifest,
        )
        write_audit(audit, args.output_dir)
    except (
        OSError,
        ReadinessAuditError,
        asyncpg.PostgresError,
        csv.Error,
        json.JSONDecodeError,
        UnicodeError,
    ) as exc:
        print(f"readiness audit failed: {exc}")
        return 2
    print(
        json.dumps(
            {
                "eligible": audit["eligible_for_strict_hcmo_export"],
                "output_dir": str(args.output_dir),
                "summary": audit["summary"],
            }
        )
    )
    return 0 if audit["eligible_for_strict_hcmo_export"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
