"""Application service wrapping UDWA's DVC API and ingestion functions."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd
from pydantic import ValidationError

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


def _request_fingerprint(request: DVCImportRequest) -> str:
    """Return a stable, credential-free identity for one bounded DVC import."""

    canonical = {
        "aggregation": request.aggregation,
        "cage_ids": sorted(request.cage_ids),
        "connection_id": request.connection_id,
        "metric_id": request.metric_id,
        "start": request.start,
        "stop": request.stop,
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
        request_fingerprint = _request_fingerprint(request)
        reused = self._find_reusable_dataset(request, request_fingerprint)
        if reused is not None:
            return reused

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
                request_fingerprint=request_fingerprint,
            )
        except Exception as exc:  # noqa: BLE001
            shutil.rmtree(dataset_dir, ignore_errors=True)
            message = redact_sensitive_text(str(exc), secrets=(connection.api_key,))
            raise DVCAcquisitionError(f"DVC dataset import failed: {message}") from exc

    def _find_reusable_dataset(
        self,
        request: DVCImportRequest,
        request_fingerprint: str,
    ) -> DVCDatasetResult | None:
        """Reuse one verified job-local import and fail closed on matching damage."""

        datasets_root = self.job_dir / "dvc_datasets"
        if not datasets_root.is_dir():
            return None

        matches: list[tuple[Path, dict[str, Any]]] = []
        matching_errors: list[str] = []
        for manifest_path in sorted(datasets_root.glob("*/manifest.json")):
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                # Without a readable request or fingerprint this artifact cannot
                # be identified as a match and is not a reason to block a distinct import.
                continue
            if not isinstance(payload, dict):
                continue

            stored_fingerprint = payload.get("request_fingerprint")
            raw_request = payload.get("request")
            try:
                stored_request = DVCImportRequest.model_validate(raw_request)
            except ValidationError as exc:
                if stored_fingerprint == request_fingerprint:
                    matching_errors.append(
                        f"{manifest_path}: matching fingerprint has an invalid request "
                        f"({exc.errors()[0]['msg']})"
                    )
                continue

            derived_fingerprint = _request_fingerprint(stored_request)
            if stored_fingerprint is not None and stored_fingerprint != derived_fingerprint:
                if (
                    stored_fingerprint == request_fingerprint
                    or derived_fingerprint == request_fingerprint
                ):
                    matching_errors.append(
                        f"{manifest_path}: stored fingerprint does not match its request"
                    )
                continue
            if derived_fingerprint == request_fingerprint:
                matches.append((manifest_path, payload))

        if matching_errors:
            details = "; ".join(matching_errors)
            raise DVCAcquisitionError(
                "Existing DVC import matching this request is corrupt; refusing vendor "
                f"re-import: {details}"
            )
        if len(matches) > 1:
            dataset_ids = ", ".join(path.parent.name for path, _ in matches)
            raise DVCAcquisitionError(
                "Multiple existing DVC imports match this request; refusing ambiguous "
                f"reuse or vendor re-import: {dataset_ids}"
            )
        if not matches:
            return None

        manifest_path, payload = matches[0]
        try:
            return self._validate_reusable_dataset(
                manifest_path=manifest_path,
                payload=payload,
                request=request,
                request_fingerprint=request_fingerprint,
            )
        except (OSError, UnicodeError, ValueError) as exc:
            raise DVCAcquisitionError(
                "Existing DVC import matching this request failed integrity checks; "
                f"refusing vendor re-import: {manifest_path}: {exc}"
            ) from exc

    def _validate_reusable_dataset(
        self,
        *,
        manifest_path: Path,
        payload: dict[str, Any],
        request: DVCImportRequest,
        request_fingerprint: str,
    ) -> DVCDatasetResult:
        dataset_dir = manifest_path.parent.resolve()
        datasets_root = (self.job_dir / "dvc_datasets").resolve()
        if dataset_dir.parent != datasets_root:
            raise ValueError("manifest is outside the job-local DVC dataset root")

        result = DVCDatasetResult.model_validate(payload.get("result"))
        if result.dataset_id != dataset_dir.name:
            raise ValueError("result dataset_id does not match its dataset directory")

        result_request = DVCImportRequest(
            connection_id=result.connection_id,
            cage_ids=result.cage_ids,
            metric_id=result.metric_id,
            start=request.start,
            stop=request.stop,
            aggregation=result.aggregation,
        )
        if _request_fingerprint(result_request) != request_fingerprint:
            raise ValueError("result metadata does not match the import request")
        if result.request_fingerprint not in (None, request_fingerprint):
            raise ValueError("result fingerprint does not match the import request")

        roles = [asset.role for asset in result.assets]
        for required_role in ("normalized_measurements", "normalized_events"):
            if roles.count(required_role) != 1:
                raise ValueError(f"result must contain exactly one {required_role} asset")
        if len(roles) != len(set(roles)):
            raise ValueError("result contains duplicate asset roles")

        for asset in result.assets:
            path = (self.job_dir / asset.relative_path).resolve()
            if dataset_dir not in path.parents:
                raise ValueError(f"asset path escapes its dataset directory: {asset.relative_path}")
            if not path.is_file():
                raise ValueError(f"recorded asset is missing: {asset.relative_path}")
            if path.stat().st_size != asset.bytes:
                raise ValueError(f"recorded asset size differs: {asset.relative_path}")
            if _sha256(path) != asset.sha256:
                raise ValueError(f"recorded asset hash differs: {asset.relative_path}")

        manifest_asset = _asset(dataset_dir, manifest_path.resolve(), "manifest")
        assets = [asset for asset in result.assets if asset.role != "manifest"]
        assets.append(manifest_asset)
        return result.model_copy(
            update={
                "assets": assets,
                "request_fingerprint": request_fingerprint,
                "reused": True,
            }
        )

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
        request_fingerprint: str | None = None,
    ) -> DVCDatasetResult:
        request_fingerprint = request_fingerprint or _request_fingerprint(request)
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
            request_fingerprint=request_fingerprint,
            reused=False,
        )

        manifest_path = dataset_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "schema": "openscientist-dvc-dataset/0.1",
                    "request_fingerprint": request_fingerprint,
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
