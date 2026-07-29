"""Application service wrapping UDWA's DVC API and ingestion functions."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd

from openscientist.integrations.dvc.credentials import (
    DVCConnectionProvider,
    EnvironmentDVCConnectionProvider,
)
from openscientist.integrations.dvc.models import (
    AssetRole,
    DVCAsset,
    DVCDatasetInspection,
    DVCDatasetResult,
    DVCImportRequest,
)
from openscientist.integrations.dvc.security import (
    redact_sensitive_data,
    redact_sensitive_text,
)


class DVCAcquisitionError(RuntimeError):
    """Stable OpenScientist-facing acquisition failure."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _asset(dataset_dir: Path, path: Path, role: AssetRole) -> DVCAsset:
    return DVCAsset(
        asset_id=f"asset-{_sha256(path)[:20]}",
        role=role,
        relative_path=str(path.relative_to(dataset_dir.parent.parent)),
        sha256=_sha256(path),
        bytes=path.stat().st_size,
    )


def _safe_vendor_state(state: dict[str, Any], *, secrets: tuple[str, ...] = ()) -> dict[str, Any]:
    allowed = {"taskId", "state", "fileSize", "errorMessage"}
    safe = {key: value for key, value in state.items() if key in allowed}
    return dict(redact_sensitive_data(safe, secrets=secrets))


class DVCAcquisitionService:
    def __init__(
        self,
        job_dir: Path,
        connection_provider: DVCConnectionProvider | None = None,
    ) -> None:
        self.job_dir = Path(job_dir)
        self.connection_provider = connection_provider or EnvironmentDVCConnectionProvider()

    def _connection(self, connection_id: str):
        return self.connection_provider.resolve(connection_id)

    def test_connection(self, connection_id: str) -> dict[str, Any]:
        connection = self._connection(connection_id)
        try:
            from udwa.ingest import test_api_connection  # type: ignore[import-untyped]

            accepted = bool(
                test_api_connection(base_url=connection.base_url, api_key=connection.api_key)
            )
        except Exception as exc:  # noqa: BLE001
            message = redact_sensitive_text(str(exc), secrets=(connection.api_key,))
            raise DVCAcquisitionError(f"DVC connection test failed: {message}") from exc
        return {"connection_id": connection_id, "accepted": accepted}

    def list_metrics(self, connection_id: str) -> list[dict[str, Any]]:
        connection = self._connection(connection_id)
        try:
            from udwa.ingest import get_metrics_list  # type: ignore[import-untyped]

            metrics = get_metrics_list(base_url=connection.base_url, api_key=connection.api_key)
        except Exception as exc:  # noqa: BLE001
            message = redact_sensitive_text(str(exc), secrets=(connection.api_key,))
            raise DVCAcquisitionError(f"DVC metric discovery failed: {message}") from exc
        return [
            dict(redact_sensitive_data(dict(item), secrets=(connection.api_key,)))
            for item in metrics
        ]

    def search_cages(self, connection_id: str, patterns: list[str]) -> list[dict[str, Any]]:
        if not patterns or any(not pattern.strip() for pattern in patterns):
            raise ValueError("At least one non-empty cage search pattern is required.")
        connection = self._connection(connection_id)
        try:
            from udwa.ingest import search_cages_list  # type: ignore[import-untyped]

            cages = search_cages_list(
                patterns=[pattern.strip() for pattern in patterns],
                base_url=connection.base_url,
                api_key=connection.api_key,
            )
        except Exception as exc:  # noqa: BLE001
            message = redact_sensitive_text(str(exc), secrets=(connection.api_key,))
            raise DVCAcquisitionError(f"DVC cage search failed: {message}") from exc
        return [
            dict(redact_sensitive_data(dict(item), secrets=(connection.api_key,))) for item in cages
        ]

    def import_dataset(self, request: DVCImportRequest) -> DVCDatasetResult:
        connection = self._connection(request.connection_id)
        dataset_id = f"dvc-{uuid4()}"
        dataset_dir = self.job_dir / "dvc_datasets" / dataset_id
        dataset_dir.mkdir(parents=True, exist_ok=False)

        try:
            from udwa.ingest import fetch_api_bundle  # type: ignore[import-untyped]

            measurements, events, warnings, state = fetch_api_bundle(
                base_url=connection.base_url,
                api_key=connection.api_key,
                cage_ids=request.cage_ids,
                metric_ids=[request.metric_id],
                start=request.start,
                stop=request.stop,
                aggr_type=request.aggregation,
                partition_type="SINGLE_FILE",
                output_filename=dataset_id,
                resource_type="CAGE",
                workdir=str(dataset_dir),
            )
            return self._persist(
                dataset_id=dataset_id,
                dataset_dir=dataset_dir,
                request=request,
                measurements=measurements,
                events=events,
                warnings=[
                    redact_sensitive_text(str(item), secrets=(connection.api_key,))
                    for item in warnings
                ],
                state=state,
                sensitive_values=(connection.api_key,),
            )
        except Exception as exc:  # noqa: BLE001
            shutil.rmtree(dataset_dir, ignore_errors=True)
            message = redact_sensitive_text(str(exc), secrets=(connection.api_key,))
            raise DVCAcquisitionError(f"DVC dataset import failed: {message}") from exc

    def _persist(
        self,
        *,
        dataset_id: str,
        dataset_dir: Path,
        request: DVCImportRequest,
        measurements: pd.DataFrame,
        events: pd.DataFrame,
        warnings: list[str],
        state: dict[str, Any],
        sensitive_values: tuple[str, ...] = (),
    ) -> DVCDatasetResult:
        measurements_path = dataset_dir / "measurements.csv"
        events_path = dataset_dir / "events.csv"
        measurements.to_csv(measurements_path, index=False)
        events.to_csv(events_path, index=False)

        assets: list[DVCAsset] = [
            _asset(dataset_dir, measurements_path, "normalized_measurements"),
            _asset(dataset_dir, events_path, "normalized_events"),
        ]
        output_path = state.get("outputPath")
        if output_path:
            raw_path = Path(str(output_path))
            if raw_path.exists() and raw_path.is_file():
                destination = dataset_dir / "raw_export.zip"
                if raw_path.resolve() != destination.resolve():
                    shutil.copy2(raw_path, destination)
                else:
                    destination = raw_path
                assets.insert(0, _asset(dataset_dir, destination, "raw_export"))

        timestamp_column = next(
            (name for name in ("timestamp_utc", "timestamp") if name in measurements.columns),
            None,
        )
        start_utc = stop_utc = None
        if timestamp_column and not measurements.empty:
            parsed = pd.to_datetime(
                measurements[timestamp_column], errors="coerce", utc=True
            ).dropna()
            if not parsed.empty:
                start_utc = parsed.min().isoformat()
                stop_utc = parsed.max().isoformat()

        inspection = DVCDatasetInspection(
            row_count=len(measurements),
            event_count=len(events),
            columns=[str(column) for column in measurements.columns],
            event_columns=[str(column) for column in events.columns],
            start_utc=start_utc,
            stop_utc=stop_utc,
            warnings=[str(item) for item in warnings],
        )
        result = DVCDatasetResult(
            dataset_id=dataset_id,
            connection_id=request.connection_id,
            metric_id=request.metric_id,
            cage_ids=request.cage_ids,
            aggregation=request.aggregation,
            assets=assets,
            inspection=inspection,
            vendor_state=_safe_vendor_state(state, secrets=sensitive_values),
        )

        manifest_path = dataset_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "schema": "openscientist-dvc-dataset/0.1",
                    "request": request.model_dump(),
                    "result": result.model_dump(),
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        result.assets.append(_asset(dataset_dir, manifest_path, "manifest"))
        return result
