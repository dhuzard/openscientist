"""Deterministic import and analysis for derived open-field tracking CSVs."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import shutil
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from io import StringIO
from pathlib import Path
from statistics import median
from typing import Any

from pydantic import ValidationError

from openscientist.assays import (
    AnalysisRunStage,
    AnalysisRunStore,
    EvidenceArtifact,
)
from openscientist.integrations.open_field.models import (
    OpenFieldAnalysisRequest,
    OpenFieldAnalysisResult,
    OpenFieldDatasetResult,
    OpenFieldImportMetadata,
    OpenFieldImportRequest,
)
from openscientist.integrations.open_field.validators import (
    validate_import,
)

_REQUIRED_COLUMNS = ("subject_id", "session_id", "timestamp", "x", "y")
_ALLOWED_COLUMNS = frozenset((*_REQUIRED_COLUMNS, "zone"))


class OpenFieldAnalysisError(ValueError):
    """Stable fail-closed error for invalid open-field input or analysis."""


@dataclass(frozen=True, slots=True)
class _Observation:
    subject_id: str
    session_id: str
    timestamp: Decimal
    x: Decimal
    y: Decimal
    zone: str | None


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _canonical_decimal(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _decimal(value: str, *, row_number: int, field: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise OpenFieldAnalysisError(f"Row {row_number}: {field} is not numeric.") from exc
    if not parsed.is_finite():
        raise OpenFieldAnalysisError(f"Row {row_number}: {field} must be finite.")
    return parsed


def _parse_tracking(content: bytes) -> tuple[_Observation, ...]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise OpenFieldAnalysisError("Tracking CSV must be UTF-8 encoded.") from exc
    reader = csv.DictReader(StringIO(text))
    if reader.fieldnames is None:
        raise OpenFieldAnalysisError("Tracking CSV requires a header row.")
    if len(reader.fieldnames) != len(set(reader.fieldnames)):
        raise OpenFieldAnalysisError("Tracking CSV contains duplicate column names.")
    columns = set(reader.fieldnames)
    missing = set(_REQUIRED_COLUMNS) - columns
    unexpected = columns - _ALLOWED_COLUMNS
    if missing:
        raise OpenFieldAnalysisError(f"Tracking CSV is missing columns: {sorted(missing)}")
    if unexpected:
        raise OpenFieldAnalysisError(
            f"Tracking CSV contains unsupported columns: {sorted(unexpected)}"
        )

    observations: list[_Observation] = []
    seen: set[tuple[str, str, Decimal]] = set()
    for row_number, row in enumerate(reader, start=2):
        subject_id = (row.get("subject_id") or "").strip()
        session_id = (row.get("session_id") or "").strip()
        if not subject_id or not session_id:
            raise OpenFieldAnalysisError(
                f"Row {row_number}: subject_id and session_id are required."
            )
        timestamp = _decimal(row.get("timestamp") or "", row_number=row_number, field="timestamp")
        if timestamp < 0:
            raise OpenFieldAnalysisError(f"Row {row_number}: timestamp must be non-negative.")
        x = _decimal(row.get("x") or "", row_number=row_number, field="x")
        y = _decimal(row.get("y") or "", row_number=row_number, field="y")
        key = (subject_id, session_id, timestamp)
        if key in seen:
            raise OpenFieldAnalysisError(
                "Duplicate subject-session timestamp: "
                f"{subject_id!r}/{session_id!r}/{_canonical_decimal(timestamp)}."
            )
        seen.add(key)
        zone_value = (row.get("zone") or "").strip() or None
        observations.append(_Observation(subject_id, session_id, timestamp, x, y, zone_value))
    if not observations:
        raise OpenFieldAnalysisError("Tracking CSV contains no observations.")
    observations.sort(key=lambda item: (item.subject_id, item.session_id, item.timestamp))
    return tuple(observations)


def _tracking_csv(observations: Iterable[_Observation], *, include_zone: bool) -> bytes:
    rows = ["subject_id,session_id,timestamp,x,y" + (",zone" if include_zone else "")]
    for item in observations:
        values = [
            item.subject_id,
            item.session_id,
            _canonical_decimal(item.timestamp),
            _canonical_decimal(item.x),
            _canonical_decimal(item.y),
        ]
        if include_zone:
            values.append(item.zone or "")
        # IDs and zones are round-tripped through the standard CSV writer.
        buffer = StringIO(newline="")
        csv.writer(buffer, lineterminator="").writerow(values)
        rows.append(buffer.getvalue())
    return ("\n".join(rows) + "\n").encode("utf-8")


def _group_sessions(
    observations: Iterable[_Observation],
) -> dict[tuple[str, str], list[_Observation]]:
    grouped: dict[tuple[str, str], list[_Observation]] = defaultdict(list)
    for item in observations:
        grouped[(item.subject_id, item.session_id)].append(item)
    return dict(grouped)


def _atomic_write(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(content)
    os.replace(temporary, path)


class OpenFieldAnalysisService:
    """Job-scoped service consumed by the generic assay gateway."""

    def __init__(self, job_dir: Path) -> None:
        self.job_dir = Path(job_dir).resolve()

    def _job_path(self, relative_path: str, *, must_exist: bool = False) -> Path:
        raw = Path(relative_path)
        if raw.is_absolute() or ".." in raw.parts:
            raise OpenFieldAnalysisError("Path must remain inside the job directory.")
        candidate = (self.job_dir / raw).resolve()
        try:
            candidate.relative_to(self.job_dir)
        except ValueError as exc:
            raise OpenFieldAnalysisError("Path must remain inside the job directory.") from exc
        if must_exist and not candidate.is_file():
            raise OpenFieldAnalysisError(f"Job-local file does not exist: {relative_path}")
        return candidate

    def import_dataset(self, request: OpenFieldImportRequest) -> OpenFieldDatasetResult:
        source = self._job_path(request.source_relative_path, must_exist=True)
        raw_content = source.read_bytes()
        observations = _parse_tracking(raw_content)
        metadata_payload = request.metadata.model_dump(mode="json")
        fingerprint = _sha256_bytes(raw_content + _canonical_json(metadata_payload))
        dataset_id = f"open-field-{fingerprint[:24]}"
        dataset_dir = self._job_path(f"open_field_datasets/{dataset_id}")
        normalized = dataset_dir / "tracking.csv"
        manifest_path = dataset_dir / "manifest.json"
        include_zone = any(item.zone is not None for item in observations)
        normalized_content = _tracking_csv(observations, include_zone=include_zone)
        normalized_sha256 = _sha256_bytes(normalized_content)
        groups = _group_sessions(observations)
        manifest: dict[str, Any] = {
            "schema_id": "openscientist-open-field-dataset/1.0",
            "dataset_id": dataset_id,
            "source_sha256": _sha256_bytes(raw_content),
            "request_fingerprint": fingerprint,
            "normalized_relative_path": str(normalized.relative_to(self.job_dir)).replace(
                "\\", "/"
            ),
            "normalized_sha256": normalized_sha256,
            "row_count": len(observations),
            "subject_count": len({item.subject_id for item in observations}),
            "session_count": len(groups),
            "has_zone": include_zone,
            "experimental_unit": "subject",
            "observational_unit": "subject_session",
            "analysis_unit": "subject",
            "metadata": metadata_payload,
        }
        if not validate_import(manifest).passed:
            raise OpenFieldAnalysisError("Normalized dataset failed its executable validator.")
        manifest_content = _canonical_json(manifest)
        if dataset_dir.exists():
            if not normalized.is_file() or not manifest_path.is_file():
                raise OpenFieldAnalysisError("Matching deterministic import is incomplete.")
            if (
                normalized.read_bytes() != normalized_content
                or manifest_path.read_bytes() != manifest_content
            ):
                raise OpenFieldAnalysisError(
                    "Matching deterministic import failed integrity verification."
                )
        else:
            dataset_dir.mkdir(parents=True, exist_ok=False)
            try:
                _atomic_write(normalized, normalized_content)
                _atomic_write(manifest_path, manifest_content)
            except Exception:
                shutil.rmtree(dataset_dir)
                raise
        return OpenFieldDatasetResult(
            dataset_id=dataset_id,
            normalized_relative_path=manifest["normalized_relative_path"],
            manifest_relative_path=str(manifest_path.relative_to(self.job_dir)).replace("\\", "/"),
            normalized_sha256=normalized_sha256,
            row_count=len(observations),
            subject_count=manifest["subject_count"],
            session_count=len(groups),
            metadata=request.metadata,
        )

    def check_data_sanity(self, request: OpenFieldAnalysisRequest) -> OpenFieldAnalysisResult:
        manifest, observations = self._load_dataset(request.dataset_id)
        payload, passed = self._sanity_result(manifest, observations)
        store = self._require_run(request, "check_data_sanity")
        return self._persist_analysis(
            request,
            "check_data_sanity",
            payload,
            manifest=manifest,
            store=store,
            passed=passed,
        )

    def _sanity_result(
        self,
        manifest: Mapping[str, Any],
        observations: tuple[_Observation, ...],
    ) -> tuple[dict[str, Any], bool]:
        metadata = OpenFieldImportMetadata.model_validate(manifest["metadata"])
        sessions = _group_sessions(observations)
        sampling: list[dict[str, Any]] = []
        all_within = True
        for (subject_id, session_id), rows in sorted(sessions.items()):
            intervals = [
                float(right.timestamp - left.timestamp)
                for left, right in zip(rows, rows[1:], strict=False)
            ]
            observed_rate = None if not intervals else 1.0 / median(intervals)
            relative_error = (
                None
                if observed_rate is None
                else abs(observed_rate - metadata.frame_rate_hz) / metadata.frame_rate_hz
            )
            within = (
                relative_error is not None
                and relative_error <= metadata.frame_rate_tolerance_fraction
            )
            all_within = all_within and within
            sampling.append(
                {
                    "subject_id": subject_id,
                    "session_id": session_id,
                    "observation_count": len(rows),
                    "observed_frame_rate_hz": observed_rate,
                    "relative_error": relative_error,
                    "within_tolerance": within,
                }
            )
        payload = {
            "clock_synchronized": metadata.clock_synchronized,
            "declared_frame_rate_hz": metadata.frame_rate_hz,
            "sampling_rate_within_tolerance": all_within,
            "subject_count": len({item.subject_id for item in observations}),
            "session_count": len(sessions),
            "observation_count": len(observations),
            "session_sampling": sampling,
        }
        passed = metadata.clock_synchronized and all_within
        return payload, passed

    def summarize_distance(self, request: OpenFieldAnalysisRequest) -> OpenFieldAnalysisResult:
        manifest, observations = self._load_analysis_ready(request)
        store = self._require_run(request, "summarize_distance")
        metadata = OpenFieldImportMetadata.model_validate(manifest["metadata"])
        session_rows: list[dict[str, Any]] = []
        subject_totals: dict[str, float] = defaultdict(float)
        subject_sessions: dict[str, int] = defaultdict(int)
        for (subject_id, session_id), rows in sorted(_group_sessions(observations).items()):
            distance = math.fsum(
                math.hypot(float(right.x - left.x), float(right.y - left.y))
                for left, right in zip(rows, rows[1:], strict=False)
            )
            session_rows.append(
                {
                    "subject_id": subject_id,
                    "session_id": session_id,
                    "distance": distance,
                    "segment_count": max(0, len(rows) - 1),
                }
            )
            subject_totals[subject_id] += distance
            subject_sessions[subject_id] += 1
        subject_rows = [
            {
                "subject_id": subject_id,
                "distance": subject_totals[subject_id],
                "session_count": subject_sessions[subject_id],
            }
            for subject_id in sorted(subject_totals)
        ]
        payload = {
            "coordinate_unit": metadata.coordinate_unit,
            "observational_unit": "subject_session",
            "analysis_unit": "subject",
            "session_distance": session_rows,
            "subject_distance": subject_rows,
        }
        return self._persist_analysis(
            request,
            "summarize_distance",
            payload,
            manifest=manifest,
            store=store,
            passed=True,
        )

    def summarize_zone_occupancy(
        self, request: OpenFieldAnalysisRequest
    ) -> OpenFieldAnalysisResult:
        manifest, observations = self._load_analysis_ready(request)
        store = self._require_run(request, "summarize_zone_occupancy")
        if any(item.zone is None for item in observations):
            raise OpenFieldAnalysisError(
                "Zone occupancy requires a non-empty zone label for every observation."
            )
        session_rows: list[dict[str, Any]] = []
        subject_zones: dict[tuple[str, str], float] = defaultdict(float)
        subject_duration: dict[str, float] = defaultdict(float)
        subject_sessions: dict[str, set[str]] = defaultdict(set)
        for (subject_id, session_id), rows in sorted(_group_sessions(observations).items()):
            zone_duration: dict[str, float] = defaultdict(float)
            total = 0.0
            for left, right in zip(rows, rows[1:], strict=False):
                duration = float(right.timestamp - left.timestamp)
                zone_duration[left.zone or ""] += duration
                total += duration
            for zone in sorted(zone_duration):
                duration = zone_duration[zone]
                session_rows.append(
                    {
                        "subject_id": subject_id,
                        "session_id": session_id,
                        "zone": zone,
                        "duration_seconds": duration,
                        "proportion": duration / total if total else 0.0,
                    }
                )
                subject_zones[(subject_id, zone)] += duration
            subject_duration[subject_id] += total
            subject_sessions[subject_id].add(session_id)
        subject_rows = [
            {
                "subject_id": subject_id,
                "zone": zone,
                "duration_seconds": duration,
                "proportion": duration / subject_duration[subject_id]
                if subject_duration[subject_id]
                else 0.0,
                "session_count": len(subject_sessions[subject_id]),
            }
            for (subject_id, zone), duration in sorted(subject_zones.items())
        ]
        payload = {
            "duration_method": "left_sample_interval_weighted",
            "observational_unit": "subject_session",
            "analysis_unit": "subject",
            "session_zone_occupancy": session_rows,
            "subject_zone_occupancy": subject_rows,
        }
        return self._persist_analysis(
            request,
            "summarize_zone_occupancy",
            payload,
            manifest=manifest,
            store=store,
            passed=True,
        )

    def _load_dataset(self, dataset_id: str) -> tuple[dict[str, Any], tuple[_Observation, ...]]:
        dataset_dir = self._job_path(f"open_field_datasets/{dataset_id}")
        manifest_path = dataset_dir / "manifest.json"
        normalized_path = dataset_dir / "tracking.csv"
        if not manifest_path.is_file() or not normalized_path.is_file():
            raise OpenFieldAnalysisError(f"Open-field dataset is unavailable: {dataset_id}")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise OpenFieldAnalysisError("Open-field manifest is unreadable.") from exc
        if (
            not isinstance(manifest, dict)
            or manifest.get("schema_id") != "openscientist-open-field-dataset/1.0"
            or manifest.get("dataset_id") != dataset_id
            or manifest.get("analysis_unit") != "subject"
        ):
            raise OpenFieldAnalysisError("Open-field manifest identity is invalid.")
        if _sha256_file(normalized_path) != manifest.get("normalized_sha256"):
            raise OpenFieldAnalysisError("Normalized tracking failed integrity verification.")
        try:
            OpenFieldImportMetadata.model_validate(manifest.get("metadata"))
        except ValidationError as exc:
            raise OpenFieldAnalysisError("Open-field manifest metadata is invalid.") from exc
        observations = _parse_tracking(normalized_path.read_bytes())
        groups = _group_sessions(observations)
        expected_path = str(normalized_path.relative_to(self.job_dir)).replace("\\", "/")
        if (
            manifest.get("normalized_relative_path") != expected_path
            or manifest.get("row_count") != len(observations)
            or manifest.get("subject_count") != len({item.subject_id for item in observations})
            or manifest.get("session_count") != len(groups)
            or not validate_import(manifest).passed
        ):
            raise OpenFieldAnalysisError("Open-field manifest failed semantic verification.")
        return manifest, observations

    def _load_analysis_ready(
        self, request: OpenFieldAnalysisRequest
    ) -> tuple[dict[str, Any], tuple[_Observation, ...]]:
        manifest, observations = self._load_dataset(request.dataset_id)
        _payload, passed = self._sanity_result(manifest, observations)
        if not passed:
            raise OpenFieldAnalysisError(
                "Dataset failed clock or sampling-rate sanity checks; analysis is blocked."
            )
        return manifest, observations

    def _require_run(
        self,
        request: OpenFieldAnalysisRequest,
        operation_id: str,
    ) -> AnalysisRunStore:
        from openscientist.integrations.open_field.adapter import (
            OPEN_FIELD_ADAPTER,
            open_field_contract_sha256,
        )

        contract = OPEN_FIELD_ADAPTER.require_operation(operation_id)
        store = AnalysisRunStore.for_analysis(
            self.job_dir,
            study_id=request.study_id,
            assay_id="open-field",
            dataset_id=request.dataset_id,
            operation_id=operation_id,
            context_sha256=request.context_sha256,
            parameters_sha256=request.parameters_sha256,
        )
        if store.run_id != request.run_id:
            raise OpenFieldAnalysisError(
                "run_id does not match the dataset, operation, context, and parameters."
            )
        state = store.load()
        if not contract.approval_required:
            if state.current_stage is AnalysisRunStage.INITIALIZED:
                state = store.record_dataset(request.dataset_id, actor="open_field_service")
            if state.current_stage is AnalysisRunStage.ACQUIRED:
                state = store.record_checkpoint(
                    f"open-field-input-{request.context_sha256[:24]}",
                    is_pre=True,
                    context_sha256=request.context_sha256,
                    actor="open_field_service",
                )
            if state.current_stage not in {
                AnalysisRunStage.PRE_ASSESSED,
                AnalysisRunStage.ANALYZED,
            }:
                raise OpenFieldAnalysisError(
                    f"Unapproved operation cannot execute from run stage {state.current_stage.value!r}."
                )
            return store

        checkpoint_id = f"open-field-pre-{operation_id}-{request.context_sha256[:16]}"
        if state.current_stage is AnalysisRunStage.INITIALIZED:
            state = store.record_dataset(request.dataset_id, actor="open_field_service")
        if state.current_stage is AnalysisRunStage.ACQUIRED:
            state = store.record_checkpoint(
                checkpoint_id,
                is_pre=True,
                context_sha256=request.context_sha256,
                actor="open_field_service",
            )
        elif (
            state.current_stage is AnalysisRunStage.PRE_ASSESSED
            and checkpoint_id not in state.checkpoints
        ):
            state = store.record_checkpoint(
                checkpoint_id,
                is_pre=True,
                context_sha256=request.context_sha256,
                actor="open_field_service",
            )
        if state.current_stage is AnalysisRunStage.PRE_ASSESSED:
            state = store.transition(
                AnalysisRunStage.PENDING_APPROVAL,
                "open_field_service",
                idempotency_key=f"pending-approval:{checkpoint_id}",
                details={
                    "checkpoint_id": checkpoint_id,
                    "dataset_id": request.dataset_id,
                    "operation_id": operation_id,
                },
            )
        if state.current_stage not in {AnalysisRunStage.APPROVED, AnalysisRunStage.ANALYZED}:
            raise OpenFieldAnalysisError(
                "Approval-required operation needs an approved analysis run."
            )
        contract_sha256 = open_field_contract_sha256(operation_id)
        matching_decisions = [
            decision
            for decision in state.approval_decisions
            if decision.decision == "approved"
            and decision.run_id == request.run_id
            and decision.assay_id == "open-field"
            and decision.dataset_id == request.dataset_id
            and decision.operation_id == operation_id
            and decision.contract_sha256 == contract_sha256
            and decision.context_sha256 == request.context_sha256
            and decision.parameters_sha256 == request.parameters_sha256
        ]
        if not matching_decisions:
            raise OpenFieldAnalysisError(
                "Analysis run lacks an approval bound to the exact contract, context, and parameters."
            )
        return store

    def _persist_analysis(
        self,
        request: OpenFieldAnalysisRequest,
        operation_id: str,
        result: dict[str, Any],
        *,
        manifest: Mapping[str, Any],
        store: AnalysisRunStore,
        passed: bool,
    ) -> OpenFieldAnalysisResult:
        relative = f"open_field_analyses/{request.run_id}/{operation_id}/result.json"
        path = self._job_path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_id": "openscientist-open-field-analysis/1.0",
            "dataset_id": request.dataset_id,
            "run_id": request.run_id,
            "operation_id": operation_id,
            "analysis_unit": "subject",
            "passed": passed,
            "result": result,
        }
        from openscientist.integrations.open_field.adapter import OPEN_FIELD_ADAPTER

        contract = OPEN_FIELD_ADAPTER.require_operation(operation_id)
        if len(contract.validator_ids) != 1:
            raise OpenFieldAnalysisError(
                f"Operation {operation_id!r} must declare exactly one executable validator."
            )
        validation = OPEN_FIELD_ADAPTER.validators[contract.validator_ids[0]](payload)
        if validation.passed != passed:
            raise OpenFieldAnalysisError(
                f"Operation {operation_id!r} and executable validator disagree."
            )
        content = _canonical_json(payload)
        provenance_relative = f"open_field_analyses/{request.run_id}/{operation_id}/provenance.json"
        provenance_path = self._job_path(provenance_relative)
        provenance = {
            "schema_id": "openscientist-open-field-provenance/1.0",
            "adapter_id": "open-field",
            "adapter_version": "1.0.0",
            "operation_id": operation_id,
            "operation_contract_version": "1.0.0",
            "dataset_id": request.dataset_id,
            "run_id": request.run_id,
            "analysis_unit": "subject",
            "normalized_sha256": manifest["normalized_sha256"],
            "metadata_sha256": _sha256_bytes(_canonical_json(manifest["metadata"])),
            "validator_id": validation.validator_id,
            "validator_version": validation.validator_version,
            "validator_passed": validation.passed,
        }
        provenance_content = _canonical_json(provenance)
        _atomic_write(path, content)
        _atomic_write(provenance_path, provenance_content)
        result_model = OpenFieldAnalysisResult.model_validate(
            {
                **payload,
                "result_relative_path": relative,
                "result_sha256": _sha256_bytes(content),
                "provenance_relative_path": provenance_relative,
                "provenance_sha256": _sha256_bytes(provenance_content),
            }
        )
        for role, artifact_path, artifact_sha256, schema_id in (
            (
                "analysis_result",
                path,
                result_model.result_sha256,
                "openscientist-open-field-analysis/1.0",
            ),
            (
                "analysis_provenance",
                provenance_path,
                result_model.provenance_sha256,
                "openscientist-open-field-provenance/1.0",
            ),
        ):
            artifact = EvidenceArtifact(
                artifact_id=f"open-field-{role}-{artifact_sha256[:24]}",
                run_id=request.run_id,
                assay_id="open-field",
                dataset_id=request.dataset_id,
                role=role,
                relative_path=str(artifact_path.relative_to(self.job_dir)).replace("\\", "/"),
                sha256=artifact_sha256,
                bytes=artifact_path.stat().st_size,
                media_type="application/json",
                schema_id=schema_id,
            )
            existing = next(
                (
                    item
                    for item in store.load().evidence
                    if item.artifact_id == artifact.artifact_id
                ),
                None,
            )
            if existing is None:
                store.record_evidence(artifact)
            elif any(
                getattr(existing, field) != getattr(artifact, field)
                for field in (
                    "run_id",
                    "assay_id",
                    "dataset_id",
                    "role",
                    "relative_path",
                    "sha256",
                    "bytes",
                    "media_type",
                    "schema_id",
                )
            ):
                raise OpenFieldAnalysisError(
                    f"Persisted evidence identity changed for {artifact.artifact_id!r}."
                )
        store.record_execution(
            f"open-field-execution-{result_model.result_sha256[:24]}",
            dataset_id=request.dataset_id,
            operation=operation_id,
            actor="open_field_service",
        )
        return result_model
