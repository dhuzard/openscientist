"""Deterministic ingestion and cross-export validation for DVC Analytics CSV files."""

from __future__ import annotations

import hashlib
import io
import re
from collections.abc import Iterable
from pathlib import Path
from typing import BinaryIO, TextIO

import numpy as np
import pandas as pd

from openscientist.dvc.models import (
    AggregationValidation,
    ExportInspection,
    ExportType,
    GroupStatisticsValidation,
)

CSVSource = str | Path | bytes | bytearray | BinaryIO | TextIO
_TYPE1 = {"timestamp", "group", "cage", "samples", "stop_ts"}
_EVENTS = {"timestamp", "group", "cage", "rack", "position", "event"}
_META_SUFFIXES = ("_TIMESTAMP", "_AVG", "_SEM", "_QRT", "_SAMPLES")
_ELECTRODE = re.compile(r"^v_(\d+)$", re.IGNORECASE)


class DVCIngestionError(ValueError):
    """The input does not satisfy the claimed DVC export contract."""


def read_csv(source: CSVSource) -> pd.DataFrame:
    if isinstance(source, (bytes, bytearray)):
        return pd.read_csv(io.BytesIO(bytes(source)))
    return pd.read_csv(source)


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def detect_export_type(columns: Iterable[str]) -> ExportType:
    values = {str(column) for column in columns}
    if _EVENTS.issubset(values):
        return ExportType.EVENTS
    if _TYPE1.issubset(values) and any(_ELECTRODE.match(column) for column in values):
        return ExportType.TYPE1
    if any(column.endswith("_TIMESTAMP") for column in values) and any(
        column.endswith("_AVG") for column in values
    ):
        return ExportType.TYPE2
    return ExportType.UNKNOWN


def _electrodes(columns: Iterable[str]) -> list[str]:
    indexed: list[tuple[int, str]] = []
    for column in columns:
        match = _ELECTRODE.match(str(column))
        if match:
            indexed.append((int(match.group(1)), str(column)))
    return [column for _, column in sorted(indexed)]


def _timestamps(series: pd.Series) -> tuple[pd.Series, pd.Series]:
    source = series.astype("string")
    return source, pd.to_datetime(source, utc=True, errors="coerce")


def _native_bin(frame: pd.DataFrame) -> float | None:
    if frame.empty or "timestamp_utc" not in frame:
        return None
    diffs: list[float] = []
    groups = [column for column in ("cage_id", "export_group") if column in frame]
    parts = frame.groupby(groups, dropna=False) if groups else [(None, frame)]
    for _, part in parts:
        values = part["timestamp_utc"].dropna().drop_duplicates().sort_values()
        seconds = values.diff().dropna().dt.total_seconds()
        diffs.extend(float(value) for value in seconds if value > 0)
    return float(np.median(diffs)) if diffs else None


def _inspection(
    frame: pd.DataFrame, source_file: str, export_type: ExportType, warnings: list[str]
) -> ExportInspection:
    timestamps = frame.get("timestamp_utc", pd.Series(dtype="datetime64[ns, UTC]")).dropna()
    cages = frame.get("cage_id", pd.Series(dtype="string")).dropna().astype(str).unique()
    groups = frame.get("export_group", pd.Series(dtype="string")).dropna().astype(str).unique()
    return ExportInspection(
        source_file=source_file,
        export_type=export_type,
        row_count=len(frame),
        cage_ids=sorted(cages.tolist()),
        group_ids=sorted(groups.tolist()),
        started_at=timestamps.min().to_pydatetime() if not timestamps.empty else None,
        ended_at=timestamps.max().to_pydatetime() if not timestamps.empty else None,
        native_bin_seconds=_native_bin(frame),
        warnings=warnings,
    )


def normalize_type1(
    frame: pd.DataFrame,
    *,
    source_file: str,
    metric_name: str,
    expected_frequency_hz: float | None = 4.0,
) -> tuple[pd.DataFrame, ExportInspection]:
    missing = sorted(_TYPE1 - set(frame.columns))
    if missing:
        raise DVCIngestionError(f"Type 1 export is missing columns: {missing}")
    electrodes = _electrodes(frame.columns)
    if not electrodes:
        raise DVCIngestionError("Type 1 export has no v_1 ... v_12 columns")
    warnings: list[str] = []
    if len(electrodes) != 12:
        warnings.append(f"expected 12 electrode columns, found {len(electrodes)}")

    out = frame.copy()
    out["timestamp_source"], out["timestamp_utc"] = _timestamps(out["timestamp"])
    out["stop_timestamp_source"], out["stop_timestamp_utc"] = _timestamps(out["stop_ts"])
    out["export_group"] = out["group"].astype("string")
    out["cage_id"] = out["cage"].astype("string")
    out["source_file"] = source_file
    out["metric_name"] = metric_name
    out["samples"] = pd.to_numeric(out["samples"], errors="coerce")
    for column in electrodes:
        out[column] = pd.to_numeric(out[column], errors="coerce")
    out["value"] = out[electrodes].mean(axis=1, skipna=True)
    out["electrode_sd"] = out[electrodes].std(axis=1, ddof=1, skipna=True)
    out["interval_seconds"] = (
        out["stop_timestamp_utc"] - out["timestamp_utc"]
    ).dt.total_seconds()
    if expected_frequency_hz is None:
        out["expected_samples"] = np.nan
        out["coverage_fraction"] = np.nan
    else:
        out["expected_samples"] = out["interval_seconds"] * expected_frequency_hz
        out["coverage_fraction"] = out["samples"] / out["expected_samples"]
    invalid = int(out["timestamp_utc"].isna().sum())
    if invalid:
        warnings.append(f"{invalid} timestamps could not be parsed")

    columns = [
        "source_file",
        "metric_name",
        "export_group",
        "cage_id",
        "timestamp_source",
        "timestamp_utc",
        "stop_timestamp_source",
        "stop_timestamp_utc",
        "interval_seconds",
        "samples",
        "expected_samples",
        "coverage_fraction",
        "value",
        "electrode_sd",
        *electrodes,
        "day",
        "hour",
        "minute",
        "relativeTime",
    ]
    normalized = out[[column for column in columns if column in out]].copy()
    return normalized, _inspection(normalized, source_file, ExportType.TYPE1, warnings)


def _type2_prefixes(columns: Iterable[str]) -> list[str]:
    return [str(column)[: -len("_TIMESTAMP")] for column in columns if str(column).endswith("_TIMESTAMP")]


def normalize_type2(
    frame: pd.DataFrame, *, source_file: str, metric_name: str
) -> tuple[pd.DataFrame, ExportInspection]:
    prefixes = _type2_prefixes(frame.columns)
    if not prefixes:
        raise DVCIngestionError("Type 2 export has no *_TIMESTAMP columns")
    warnings: list[str] = []
    blocks: list[pd.DataFrame] = []
    shared = [column for column in ("day", "hour", "minute", "relativeTime") if column in frame]
    for prefix in prefixes:
        timestamp = f"{prefix}_TIMESTAMP"
        excluded = {f"{prefix}{suffix}" for suffix in _META_SUFFIXES}
        cage_columns = [
            column for column in frame.columns if column.startswith(f"{prefix}_") and column not in excluded
        ]
        if not cage_columns:
            warnings.append(f"group {prefix!r} has no cage columns")
            continue
        base = frame[shared].copy()
        base["timestamp_source"], base["timestamp_utc"] = _timestamps(frame[timestamp])
        for cage_column in cage_columns:
            block = base.copy()
            block["source_file"] = source_file
            block["metric_name"] = metric_name
            block["export_group"] = prefix
            block["cage_id"] = cage_column[len(prefix) + 1 :]
            block["value"] = pd.to_numeric(frame[cage_column], errors="coerce")
            block["vendor_group_avg"] = pd.to_numeric(frame.get(f"{prefix}_AVG"), errors="coerce")
            block["vendor_group_sem"] = pd.to_numeric(frame.get(f"{prefix}_SEM"), errors="coerce")
            block["vendor_group_samples"] = pd.to_numeric(
                frame.get(f"{prefix}_SAMPLES"), errors="coerce"
            )
            blocks.append(block)
    normalized = pd.concat(blocks, ignore_index=True) if blocks else pd.DataFrame()
    return normalized, _inspection(normalized, source_file, ExportType.TYPE2, warnings)


def normalize_events(
    frame: pd.DataFrame, *, source_file: str
) -> tuple[pd.DataFrame, ExportInspection]:
    missing = sorted(_EVENTS - set(frame.columns))
    if missing:
        raise DVCIngestionError(f"event export is missing columns: {missing}")
    out = frame.copy()
    out["timestamp_source"], out["timestamp_utc"] = _timestamps(out["timestamp"])
    out["export_group"] = out["group"].astype("string")
    out["cage_id"] = out["cage"].astype("string")
    out["event_code"] = out["event"].astype("string")
    out["source_file"] = source_file
    out["event_origin"] = "unknown"
    out["event_semantics_status"] = "unknown"
    columns = [
        "source_file",
        "export_group",
        "cage_id",
        "timestamp_source",
        "timestamp_utc",
        "rack",
        "position",
        "event_code",
        "event_origin",
        "event_semantics_status",
    ]
    normalized = out[columns].copy()
    return normalized, _inspection(normalized, source_file, ExportType.EVENTS, [])


def validate_type1_against_type2(
    type1: pd.DataFrame, type2: pd.DataFrame, *, tolerance: float = 1e-9
) -> AggregationValidation:
    left = type1[["timestamp_utc", "cage_id", "value"]].rename(columns={"value": "type1_value"})
    right = type2[["timestamp_utc", "cage_id", "value"]].rename(columns={"value": "type2_value"})
    merged = left.merge(right, on=["timestamp_utc", "cage_id"], how="inner").dropna()
    difference = (merged["type1_value"] - merged["type2_value"]).abs()
    matched = int((difference <= tolerance).sum())
    return AggregationValidation(
        compared_rows=len(merged),
        matched_rows=matched,
        unmatched_rows=len(merged) - matched,
        max_absolute_difference=float(difference.max()) if not difference.empty else None,
        tolerance=tolerance,
    )


def validate_type2_group_statistics(
    type2: pd.DataFrame, *, tolerance: float = 1e-9
) -> GroupStatisticsValidation:
    rows: list[dict[str, float]] = []
    for _, part in type2.groupby(["export_group", "timestamp_utc"], dropna=False):
        values = part["value"].dropna().astype(float)
        if values.empty:
            continue
        rows.append(
            {
                "vendor_avg": float(part["vendor_group_avg"].dropna().iloc[0]),
                "vendor_sem": float(part["vendor_group_sem"].dropna().iloc[0]),
                "mean": float(values.mean()),
                "sd": float(values.std(ddof=1)) if len(values) > 1 else np.nan,
                "sem": float(values.sem(ddof=1)) if len(values) > 1 else np.nan,
            }
        )
    stats = pd.DataFrame(rows)
    if stats.empty:
        return GroupStatisticsValidation(
            compared_rows=0,
            average_matches=0,
            sem_matches_conventional=0,
            sem_matches_sample_sd=0,
            undefined_sem_rows=0,
            tolerance=tolerance,
        )

    def close(left: pd.Series, right: pd.Series) -> np.ndarray:
        return np.isclose(left, right, atol=tolerance, rtol=0, equal_nan=False)

    return GroupStatisticsValidation(
        compared_rows=len(stats),
        average_matches=int(close(stats["vendor_avg"], stats["mean"]).sum()),
        sem_matches_conventional=int(close(stats["vendor_sem"], stats["sem"]).sum()),
        sem_matches_sample_sd=int(close(stats["vendor_sem"], stats["sd"]).sum()),
        undefined_sem_rows=int(stats["sem"].isna().sum()),
        tolerance=tolerance,
    )


def annotate_event_counts(type1: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """Attach same-cage event counts and codes falling inside each Type 1 interval."""

    out = type1.copy()
    counts: list[int] = []
    codes: list[str] = []
    for row in out.itertuples(index=False):
        mask = (
            (events["cage_id"] == row.cage_id)
            & (events["timestamp_utc"] >= row.timestamp_utc)
            & (events["timestamp_utc"] < row.stop_timestamp_utc)
        )
        selected = events.loc[mask, "event_code"].dropna().astype(str)
        counts.append(len(selected))
        codes.append("|".join(sorted(set(selected))))
    out["event_count"] = counts
    out["event_codes"] = codes
    return out
