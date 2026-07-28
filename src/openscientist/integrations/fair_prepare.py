"""HTTP integration with Neuronautix/FAIR-VCG-mentor.

OpenScientist treats FAIR-VCG Mentor as the authoritative FAIR/reporting-template
assessment service. This module translates versioned OpenScientist contracts into
its documented REST API and validates the returned structures.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import httpx

from openscientist.preclinical_context.models import (
    AssessmentResult,
    EvidenceStatus,
    PreclinicalStudyContext,
)

DEFAULT_FAIR_PREPARE_URL = "http://fair-vcg-mentor:8000"
FAIR_VCG_API_VERSION = "1.0.0"
FAIR_VCG_REPOSITORY = "Neuronautix/FAIR-VCG-mentor"
FAIR_VCG_PINNED_COMMIT = "11b0918c01062a0c9a388b33d28068982712d762"


class FairPrepareError(RuntimeError):
    """Stable OpenScientist-facing FAIR/PREPARE integration failure."""


class FairPrepareProvider(Protocol):
    def assess_context(
        self,
        context: PreclinicalStudyContext,
        *,
        frameworks: tuple[str, ...],
    ) -> list[AssessmentResult]: ...

    def assess_bundle(
        self,
        bundle_dir: Path,
        *,
        frameworks: tuple[str, ...],
    ) -> list[AssessmentResult]: ...


def _flatten(prefix: str, value: Any, row: dict[str, str]) -> None:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        for key, item in value.items():
            _flatten(f"{prefix}.{key}" if prefix else str(key), item, row)
        return
    if isinstance(value, list):
        row[prefix] = "; ".join(str(item) for item in value)
        return
    row[prefix] = "" if value is None else str(value)


def context_to_csv(context: PreclinicalStudyContext) -> bytes:
    row: dict[str, str] = {}
    _flatten("", context, row)
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=sorted(row))
    writer.writeheader()
    writer.writerow(row)
    return output.getvalue().encode("utf-8")


def bundle_manifest_to_csv(bundle_dir: Path) -> bytes:
    rows: list[dict[str, str]] = []
    for path in sorted(bundle_dir.rglob("*")):
        if path.is_file():
            rows.append(
                {
                    "relative_path": str(path.relative_to(bundle_dir)),
                    "bytes": str(path.stat().st_size),
                    "suffix": path.suffix.lower(),
                }
            )
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["relative_path", "bytes", "suffix"])
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def _known(value: Any) -> Any | None:
    if getattr(value, "status", EvidenceStatus.UNKNOWN) == EvidenceStatus.UNKNOWN:
        return None
    return getattr(value, "value", None)


def context_to_metadata(context: PreclinicalStudyContext) -> dict[str, Any]:
    """Crosswalk neutral study context to FAIR-VCG ARRIVE/PREPARE metadata IDs."""
    metadata: dict[str, Any] = {
        "title": f"Pre-analysis context for {context.study_id}",
        "description": _known(context.objective) or "Preclinical study planning context",
        "base_uri": f"urn:openscientist:study:{context.study_id}",
        "date_created": datetime.now(timezone.utc).date().isoformat(),
        "version": context.schema_version,
    }
    mappings = {
        "experimental_unit": _known(context.design.experimental_unit),
        "prepare_experimental_unit": _known(context.design.experimental_unit),
        "randomisation_method": _known(context.design.randomization),
        "blinding_strategy": _known(context.design.blinding),
        "exclusion_criteria": _known(context.design.exclusion_policy),
        "species": _known(context.animals.species),
        "strain": _known(context.animals.strain),
        "sex": _known(context.animals.sex),
        "age": _known(context.animals.age),
        "housing_density": _known(context.animals.occupancy),
        "housing_conditions": _known(context.environment.housing),
        "husbandry": _known(context.environment.husbandry),
        "light_dark_cycle": _known(context.environment.light_schedule),
        "timezone": _known(context.environment.timezone),
        "data_acquisition_system": _known(context.acquisition.system),
        "software_version": _known(context.acquisition.software_version),
        "temporal_resolution": _known(context.acquisition.temporal_resolution),
        "prepare_clear_hypothesis": _known(context.objective),
        "prepare_animal_characteristics": {
            "species": _known(context.animals.species),
            "strain": _known(context.animals.strain),
            "sex": _known(context.animals.sex),
            "age": _known(context.animals.age),
        },
        "prepare_acclimatisation_housing": {
            "housing": _known(context.environment.housing),
            "husbandry": _known(context.environment.husbandry),
            "light_schedule": _known(context.environment.light_schedule),
        },
    }
    for key, value in mappings.items():
        if value is None:
            continue
        if isinstance(value, dict):
            value = {k: v for k, v in value.items() if v is not None}
            if not value:
                continue
        metadata[key] = value
    if metadata.get("randomisation_method") or metadata.get("blinding_strategy"):
        metadata["prepare_randomisation_blinding_criteria"] = {
            "randomisation": metadata.get("randomisation_method"),
            "blinding": metadata.get("blinding_strategy"),
            "exclusion_criteria": metadata.get("exclusion_criteria"),
        }
    return metadata


def bundle_to_metadata(bundle_dir: Path) -> dict[str, Any]:
    files = [path for path in sorted(bundle_dir.rglob("*")) if path.is_file()]
    operations: list[str] = []
    index_path = bundle_dir / "analysis-index.json"
    if index_path.is_file():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
            operations = sorted(
                {str(item.get("operation")) for item in index if item.get("operation")}
            )
        except Exception:  # noqa: BLE001
            operations = []
    return {
        "title": "OpenScientist DVC analysis bundle",
        "description": "Traceable DVC acquisition and deterministic analysis artifact bundle.",
        "base_uri": f"urn:openscientist:dvc-bundle:{bundle_dir.name}",
        "creator": "OpenScientist",
        "date_created": datetime.now(timezone.utc).date().isoformat(),
        "version": "openscientist-dvc-bundle/0.1",
        "access_conditions": "Job-authorized access",
        "protocol_reference": ", ".join(operations) if operations else "No analysis recorded",
        "keywords": ["DVC", "preclinical", "FAIR", "provenance"],
        "bundle_file_count": len(files),
    }


class HttpFairPrepareProvider:
    """FAIR-VCG Mentor REST provider using upload, FAIR score and templates."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout: float = 60.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = (
            base_url or os.getenv("FAIR_PREPARE_URL") or DEFAULT_FAIR_PREPARE_URL
        ).rstrip("/")
        self.timeout = timeout
        self.client = client or httpx.Client(timeout=timeout)

    def assess_context(
        self,
        context: PreclinicalStudyContext,
        *,
        frameworks: tuple[str, ...] = ("prepare-v1", "arrive-v2"),
    ) -> list[AssessmentResult]:
        return self._assess_bytes(
            context_to_csv(context),
            filename="preclinical-context.csv",
            metadata=context_to_metadata(context),
            frameworks=frameworks,
        )

    def assess_bundle(
        self,
        bundle_dir: Path,
        *,
        frameworks: tuple[str, ...] = ("arrive-v2", "mnms-v1"),
    ) -> list[AssessmentResult]:
        bundle_dir = Path(bundle_dir)
        return self._assess_bytes(
            bundle_manifest_to_csv(bundle_dir),
            filename="bundle-manifest.csv",
            metadata=bundle_to_metadata(bundle_dir),
            frameworks=frameworks,
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self.client.request(method, f"{self.base_url}{path}", **kwargs)
        except Exception as exc:  # noqa: BLE001
            raise FairPrepareError(f"FAIR-VCG request failed: {exc}") from exc
        if response.status_code >= 400:
            detail = response.text[:500].strip()
            raise FairPrepareError(
                f"FAIR-VCG {method} {path} failed (HTTP {response.status_code}): {detail}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise FairPrepareError(f"FAIR-VCG {path} returned invalid JSON.") from exc

    def _assess_bytes(
        self,
        content: bytes,
        *,
        filename: str,
        metadata: dict[str, Any],
        frameworks: tuple[str, ...],
    ) -> list[AssessmentResult]:
        source_hash = hashlib.sha256(content).hexdigest()
        uploaded = self._request(
            "POST",
            "/api/upload",
            files={"file": (filename, content, "text/csv")},
        )
        dataset_id = str(uploaded["dataset_id"])
        self._request("PUT", f"/api/metadata/{dataset_id}", json=metadata)
        fair_score = self._request("GET", f"/api/fair-score/{dataset_id}")
        results = [self._fair_result(dataset_id, source_hash, fair_score)]
        for framework in frameworks:
            applied = self._request(
                "POST",
                f"/api/{dataset_id}/template/apply-from-paper",
                json={"template_id": framework},
            )
            results.append(
                self._template_result(dataset_id, source_hash, framework, applied)
            )
        return results

    @staticmethod
    def _fair_result(
        dataset_id: str,
        source_hash: str,
        score: dict[str, Any],
    ) -> AssessmentResult:
        findings: list[dict[str, Any]] = []
        for dimension in ("findable", "accessible", "interoperable", "reusable"):
            payload = score.get(dimension) or {}
            current = int(payload.get("score", 0))
            maximum = int(payload.get("max_score", 0))
            ratio = current / maximum if maximum else 0.0
            status = "satisfied" if ratio == 1 else "partial" if current > 0 else "missing"
            findings.append(
                {
                    "requirement_id": f"FAIR-{dimension[0].upper()}",
                    "status": status,
                    "recommendation": (
                        f"Score {current}/{maximum}. "
                        f"Main weakness: {payload.get('main_weakness', 'not reported')}."
                    ),
                }
            )
            for criterion, satisfied in (payload.get("criteria") or {}).items():
                findings.append(
                    {
                        "requirement_id": f"FAIR-{dimension[0].upper()}:{criterion}",
                        "status": "satisfied" if satisfied else "missing",
                        "missing_fields": [] if satisfied else [criterion],
                    }
                )
        return AssessmentResult(
            assessment_id=f"fair-vcg-{dataset_id}",
            framework="FAIR",
            framework_version=f"FAIR-VCG-mentor-api-{FAIR_VCG_API_VERSION}",
            context_hash=source_hash,
            findings=findings,
        )

    @staticmethod
    def _template_result(
        dataset_id: str,
        source_hash: str,
        framework: str,
        payload: dict[str, Any],
    ) -> AssessmentResult:
        findings: list[dict[str, Any]] = []
        for entry in payload.get("conformance_report", []):
            raw_status = str(entry.get("status") or "missing").lower()
            status = (
                raw_status
                if raw_status
                in {"satisfied", "partial", "missing", "not_applicable", "conflicting"}
                else "partial"
            )
            field_id = str(entry.get("field_id") or entry.get("id") or "unknown")
            findings.append(
                {
                    "requirement_id": field_id,
                    "status": status,
                    "missing_fields": [field_id] if status == "missing" else [],
                    "recommendation": entry.get("guidance") or entry.get("message"),
                }
            )
        return AssessmentResult(
            assessment_id=f"fair-vcg-{dataset_id}-{framework}",
            framework=framework,
            framework_version=(
                f"FAIR-VCG-mentor-template-{FAIR_VCG_PINNED_COMMIT[:12]}"
            ),
            context_hash=source_hash,
            findings=findings,
        )
