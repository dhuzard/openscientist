"""Fail-closed, content-addressed preparation for uploaded DVC exports."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

import pandas as pd

from openscientist.dvc.ingestion import (
    DVCIngestionError,
    detect_export_type,
    file_sha256,
    normalize_type1,
    normalize_type2,
    read_csv,
    type2_trace_columns,
)
from openscientist.dvc.models import (
    DVCImportSpec,
    DVCPreparedDataset,
    DVCSourceSpec,
    ExportType,
)

_PARSER_CONTRACT = "openscientist-dvc-parser/2"
_OFFSET_RE = re.compile(r"(?:Z|[+-]\d{2}:?\d{2})\s*$", re.IGNORECASE)


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


def _sha256(path: Path) -> str:
    return file_sha256(path)


def _asset_id(path: Path) -> str:
    return f"asset-{_sha256(path)[:20]}"


def _source_manifest(spec: DVCSourceSpec) -> dict[str, Any]:
    path = spec.path.resolve()
    return {
        "source_id": spec.source_id,
        "filename": path.name,
        "sha256": _sha256(path),
        "metric_name": spec.metric_name,
        "site": spec.site,
        "cohort": spec.cohort,
        "iana_timezone": spec.iana_timezone,
        "clock_correction_minutes": spec.clock_correction_minutes,
        "clock_correction_reason": spec.clock_correction_reason,
        "expected_trace_count": spec.expected_trace_count,
        "expected_trace_ids": sorted(spec.expected_trace_ids or []),
        "schema_hint": spec.schema_hint.value if spec.schema_hint else None,
    }


def _cache_key(spec: DVCImportSpec) -> tuple[str, list[dict[str, Any]]]:
    sources = [_source_manifest(source) for source in spec.sources]
    payload = {
        "schema": spec.schema_version,
        "parser_contract": _PARSER_CONTRACT,
        "strict": spec.strict,
        "sources": sorted(sources, key=lambda item: (item["source_id"], item["sha256"])),
    }
    return hashlib.sha256(_json_bytes(payload)).hexdigest(), sources


def _dataset_id(cache_key: str) -> str:
    value = cache_key[:32]
    return "dvc-" + "-".join((value[:8], value[8:12], value[12:16], value[16:20], value[20:]))


@contextmanager
def _key_lock(
    root: Path,
    cache_key: str,
    dataset_dir: Path,
    *,
    timeout_seconds: float = 30.0,
) -> Iterator[bool]:
    """Acquire one cache-key lock or wait for the owning writer to publish.

    The boolean is true only for the writer. Readers never remove another
    process's lock and may validate the completed dataset as soon as its atomic
    directory rename becomes visible.
    """
    lock = root / f".{cache_key}.lock"
    deadline = time.monotonic() + timeout_seconds
    acquired = False
    while not acquired:
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(descriptor, f"pid={os.getpid()}\n".encode())
            acquired = True
        except FileExistsError:
            if dataset_dir.is_dir():
                yield False
                return
            if time.monotonic() >= deadline:
                raise DVCIngestionError(
                    f"Timed out waiting for concurrent DVC preparation {cache_key}"
                ) from None
            time.sleep(0.05)
    try:
        os.close(descriptor)
        yield True
    finally:
        if acquired:
            lock.unlink(missing_ok=True)


def _expected_type2_traces(frame: pd.DataFrame) -> list[str]:
    return sorted(
        f"{prefix}:{column}"
        for prefix, columns in type2_trace_columns(frame.columns).items()
        for column in columns
    )


def _prepare_source(
    spec: DVCSourceSpec,
) -> tuple[pd.DataFrame, dict[str, Any], list[dict[str, Any]]]:
    frame = read_csv(spec.path)
    export_type = detect_export_type(frame.columns)
    if spec.schema_hint is not None and export_type is not spec.schema_hint:
        raise DVCIngestionError(
            f"{spec.source_id}: detected {export_type.value}, expected {spec.schema_hint.value}"
        )
    if export_type is ExportType.TYPE1:
        identity = (
            "cage" if "cage" in frame.columns else "mouse" if "mouse" in frame.columns else ""
        )
        expected_ids = sorted(frame[identity].dropna().astype(str).unique()) if identity else []
        normalized, inspection = normalize_type1(
            frame, source_file=spec.source_id, metric_name=spec.metric_name
        )
        expected_trace_labels = expected_ids
    elif export_type is ExportType.TYPE2:
        expected_trace_labels = _expected_type2_traces(frame)
        normalized, inspection = normalize_type2(
            frame, source_file=spec.source_id, metric_name=spec.metric_name
        )
        expected_ids = sorted(
            f"{group}:{cage}"
            for group, cage in normalized[["export_group", "cage_id"]]
            .drop_duplicates()
            .itertuples(index=False, name=None)
        )
    else:
        raise DVCIngestionError(
            f"{spec.source_id}: uploaded CSV is not a supported Type 1 or Type 2 DVC export"
        )

    source_text = normalized["timestamp_source"].dropna().astype(str)
    has_offset = source_text.map(lambda value: bool(_OFFSET_RE.search(value))).all()
    if not has_offset and not spec.iana_timezone:
        raise DVCIngestionError(
            f"{spec.source_id}: naive timestamps require a source-specific IANA timezone"
        )
    if not has_offset:
        local = pd.to_datetime(normalized["timestamp_source"], errors="raise")
        try:
            normalized["timestamp_utc"] = local.dt.tz_localize(
                spec.iana_timezone, ambiguous="raise", nonexistent="raise"
            ).dt.tz_convert("UTC")
        except (TypeError, ValueError) as exc:
            raise DVCIngestionError(
                f"{spec.source_id}: timestamps cannot be localized with {spec.iana_timezone}: {exc}"
            ) from exc

    observed_ids = sorted(
        f"{group}:{cage}"
        for group, cage in normalized[["export_group", "cage_id"]]
        .drop_duplicates()
        .itertuples(index=False, name=None)
    )
    declared_ids = sorted(spec.expected_trace_ids or [])
    expected_count = spec.expected_trace_count or len(expected_ids)
    observed_count = len(observed_ids)
    if expected_count != observed_count:
        raise DVCIngestionError(
            f"{spec.source_id}: expected {expected_count} physical traces, loaded {observed_count}"
        )
    if declared_ids and declared_ids != observed_ids:
        raise DVCIngestionError(f"{spec.source_id}: declared and observed trace identities differ")

    normalized = normalized.reset_index(drop=False).rename(columns={"index": "source_row"})
    normalized["source_sha256"] = _sha256(spec.path)
    normalized["site"] = spec.site
    normalized["cohort"] = spec.cohort
    normalized["source_trace_id"] = (
        normalized["export_group"].astype(str) + ":" + normalized["cage_id"].astype(str)
    )
    normalized["cage_key"] = spec.source_id + ":" + normalized["source_trace_id"]
    normalized["timestamp_local_uncorrected"] = pd.to_datetime(
        normalized["timestamp_source"], errors="coerce"
    ).map(lambda value: value.tz_localize(None) if getattr(value, "tzinfo", None) else value)
    normalized["timestamp_correction_minutes"] = spec.clock_correction_minutes
    normalized["timestamp_correction_reason"] = spec.clock_correction_reason
    normalized["timestamp_local"] = normalized["timestamp_local_uncorrected"] + pd.to_timedelta(
        spec.clock_correction_minutes, unit="m"
    )
    normalized["iana_timezone"] = spec.iana_timezone

    reconciliation = [
        {
            "source_id": spec.source_id,
            "export_type": export_type.value,
            "source_trace_id": trace_id,
            "observed": True,
            "declared": not declared_ids or trace_id in declared_ids,
            "status": "reconciled",
        }
        for trace_id in observed_ids
    ]
    report = {
        "source_id": spec.source_id,
        "export_type": export_type.value,
        "source_rows": len(frame),
        "source_columns": [str(column) for column in frame.columns],
        "header_trace_labels": expected_trace_labels,
        "expected_trace_count": expected_count,
        "observed_trace_count": observed_count,
        "native_bin_seconds": inspection.native_bin_seconds,
        "started_at": inspection.started_at,
        "ended_at": inspection.ended_at,
        "warnings": inspection.warnings,
    }
    return normalized, report, reconciliation


def _validate_existing(dataset_dir: Path, cache_key: str) -> DVCPreparedDataset:
    manifest_path = dataset_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("cache_key") != cache_key:
        raise DVCIngestionError("prepared DVC manifest cache key mismatch")
    for name, expected in manifest.get("artifact_sha256", {}).items():
        path = dataset_dir / name
        if not path.is_file() or _sha256(path) != expected:
            raise DVCIngestionError(f"prepared DVC artifact integrity failed: {name}")
    return DVCPreparedDataset(
        dataset_id=dataset_dir.name,
        cache_key=cache_key,
        manifest_relpath=f"dvc_datasets/{dataset_dir.name}/manifest.json",
        measurement_asset_id=str(manifest["measurement_asset_id"]),
        reused=True,
        trace_count=int(manifest["trace_count"]),
        row_count=int(manifest["row_count"]),
    )


def _fsync(path: Path) -> None:
    with path.open("r+b") as handle:
        os.fsync(handle.fileno())


def prepare_uploaded_dvc(job_dir: Path, spec: DVCImportSpec) -> DVCPreparedDataset:
    """Parse once, reconcile per source, and publish immutable Parquet assets."""
    job_dir = job_dir.resolve()
    cache_key, source_manifest = _cache_key(spec)
    dataset_id = _dataset_id(cache_key)
    root = job_dir / "dvc_datasets"
    root.mkdir(parents=True, exist_ok=True)
    dataset_dir = root / dataset_id
    if dataset_dir.exists():
        return _validate_existing(dataset_dir, cache_key)

    with _key_lock(root, cache_key, dataset_dir) as acquired:
        if dataset_dir.exists():
            return _validate_existing(dataset_dir, cache_key)
        if not acquired:  # defensive: a reader is returned only after publication
            raise DVCIngestionError("Concurrent DVC preparation ended without an artifact")
        staging = root / f".{dataset_id}.staging-{os.getpid()}"
        staging.mkdir(parents=False, exist_ok=False)
        try:
            frames: list[pd.DataFrame] = []
            reports: list[dict[str, Any]] = []
            reconciliation: list[dict[str, Any]] = []
            for source in spec.sources:
                frame, report, source_reconciliation = _prepare_source(source)
                frames.append(frame)
                reports.append(report)
                reconciliation.extend(source_reconciliation)
            measurements = pd.concat(frames, ignore_index=True)
            measurement_path = staging / "measurements.parquet"
            reconciliation_path = staging / "cage_reconciliation.parquet"
            input_path = staging / "input_manifest.json"
            schema_path = staging / "schema_report.json"
            measurements.to_parquet(measurement_path, index=False)
            pd.DataFrame(reconciliation).to_parquet(reconciliation_path, index=False)
            input_path.write_text(
                json.dumps(source_manifest, indent=2, sort_keys=True, default=str), encoding="utf-8"
            )
            schema_path.write_text(
                json.dumps(reports, indent=2, sort_keys=True, default=str), encoding="utf-8"
            )
            for path in (measurement_path, reconciliation_path, input_path, schema_path):
                _fsync(path)
            artifacts = {
                path.name: _sha256(path)
                for path in (measurement_path, reconciliation_path, input_path, schema_path)
            }
            manifest = {
                "schema": "openscientist-dvc-prepared/1",
                "parser_contract": _PARSER_CONTRACT,
                "cache_key": cache_key,
                "created_at": datetime.now(UTC).isoformat(),
                "measurement_asset_id": _asset_id(measurement_path),
                "measurement_relpath": "measurements.parquet",
                "trace_count": int(measurements["cage_key"].nunique()),
                "row_count": len(measurements),
                "artifact_sha256": artifacts,
                "sources": source_manifest,
            }
            manifest_path = staging / "manifest.json"
            manifest_path.write_text(
                json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
            )
            _fsync(manifest_path)
            os.replace(staging, dataset_dir)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
    return _validate_existing(dataset_dir, cache_key).model_copy(update={"reused": False})


def default_upload_spec(paths: list[Path]) -> DVCImportSpec:
    return DVCImportSpec(
        sources=[
            DVCSourceSpec(path=path, source_id=path.stem, metric_name="activity")
            for path in paths
            if path.suffix.casefold() == ".csv"
        ]
    )
