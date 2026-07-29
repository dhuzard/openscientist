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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from openscientist.preclinical_context.models import (
    AssessmentFinding,
    AssessmentResult,
    EvidenceStatus,
    PreclinicalStudyContext,
)

DEFAULT_FAIR_PREPARE_URL = "http://fair-vcg-mentor:8000"
FAIR_VCG_API_VERSION = "1.0.0"
FAIR_VCG_REPOSITORY = "Neuronautix/FAIR-VCG-mentor"
FAIR_VCG_PINNED_COMMIT = "11b0918c01062a0c9a388b33d28068982712d762"
FAIR_PREPARE_URL_ENV = "FAIR_PREPARE_URL"
FAIR_VCG_REQUIRED_TEMPLATES = ("prepare-v1", "arrive-v2", "mnms-v1")


class FairPrepareError(RuntimeError):
    """Stable OpenScientist-facing FAIR/PREPARE integration failure."""


@dataclass(frozen=True)
class FairPrepareCompatibilityReport:
    """Evidence returned only after the deployed FAIR-VCG contract is exercised."""

    api_version: str
    dataset_id: str
    templates: tuple[str, ...]


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


def validate_fair_prepare_url(value: str) -> str:
    """Validate the non-secret internal service locator before propagating it.

    Embedded user info is rejected because environment variables are visible to
    the agent container. Query strings and fragments are also disallowed so the
    value remains a service locator rather than a credential-bearing request.
    """

    candidate = value.strip().rstrip("/")
    parsed = urlsplit(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise FairPrepareError(
            "FAIR_PREPARE_URL must be an absolute http(s) service URL."
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise FairPrepareError(
            "FAIR_PREPARE_URL must not contain credentials, a query, or a fragment."
        )
    return candidate


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
        configured_url = (
            base_url or os.getenv("FAIR_PREPARE_URL") or DEFAULT_FAIR_PREPARE_URL
        )
        self.base_url = validate_fair_prepare_url(configured_url)
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
        # Import lazily: the DVC package re-exports assessment providers that
        # depend on this module, so importing its security helper at module load
        # time would create a circular initialization.
        from openscientist.integrations.dvc.security import redact_sensitive_text

        try:
            response = self.client.request(method, f"{self.base_url}{path}", **kwargs)
        except Exception as exc:  # noqa: BLE001
            detail = redact_sensitive_text(str(exc))
            raise FairPrepareError(f"FAIR-VCG request failed: {detail}") from exc
        if response.status_code >= 400:
            detail = redact_sensitive_text(response.text[:500].strip())
            raise FairPrepareError(
                f"FAIR-VCG {method} {path} failed (HTTP {response.status_code}): {detail}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise FairPrepareError(f"FAIR-VCG {path} returned invalid JSON.") from exc

    @staticmethod
    def _mapping(payload: Any, *, endpoint: str) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise FairPrepareError(
                f"FAIR-VCG {endpoint} returned an incompatible response."
            )
        return payload

    @staticmethod
    def _dataset_id(payload: dict[str, Any]) -> str:
        dataset_id = payload.get("dataset_id")
        if not isinstance(dataset_id, str) or not dataset_id.strip():
            raise FairPrepareError(
                "FAIR-VCG /api/upload response is missing a valid dataset_id."
            )
        return dataset_id

    @staticmethod
    def _validate_fair_score(payload: dict[str, Any]) -> None:
        if not isinstance(payload.get("fair_score"), (int, float)):
            raise FairPrepareError(
                "FAIR-VCG fair-score response is missing numeric fair_score."
            )
        for dimension in ("findable", "accessible", "interoperable", "reusable"):
            score = payload.get(dimension)
            if not isinstance(score, dict):
                raise FairPrepareError(
                    f"FAIR-VCG fair-score response is missing {dimension}."
                )
            if not isinstance(score.get("score"), (int, float)) or not isinstance(
                score.get("max_score"), (int, float)
            ):
                raise FairPrepareError(
                    f"FAIR-VCG fair-score {dimension} values are incompatible."
                )
            if not isinstance(score.get("criteria"), dict):
                raise FairPrepareError(
                    f"FAIR-VCG fair-score {dimension}.criteria is incompatible."
                )

    @staticmethod
    def _validate_template(
        payload: dict[str, Any],
        *,
        framework: str,
    ) -> None:
        if payload.get("template_id") != framework:
            raise FairPrepareError(
                f"FAIR-VCG template response did not confirm {framework}."
            )
        if not isinstance(payload.get("conformance_report"), list):
            raise FairPrepareError(
                f"FAIR-VCG template {framework} returned an incompatible report."
            )

    def check_compatibility(self) -> FairPrepareCompatibilityReport:
        """Exercise the pinned API contract with synthetic, non-sensitive data.

        A report is returned only when the advertised API version and every
        production endpoint/template are compatible. Any outage or mismatch
        raises :class:`FairPrepareError`, allowing deployment to fail closed.
        """

        openapi = self._mapping(
            self._request("GET", "/openapi.json"),
            endpoint="/openapi.json",
        )
        info = self._mapping(openapi.get("info"), endpoint="/openapi.json info")
        version = info.get("version")
        if version != FAIR_VCG_API_VERSION:
            raise FairPrepareError(
                "FAIR-VCG API version mismatch: "
                f"expected {FAIR_VCG_API_VERSION}, received {version!r}."
            )
        paths = self._mapping(openapi.get("paths"), endpoint="/openapi.json paths")
        required_operations = {
            "/api/upload": "post",
            "/api/metadata/{dataset_id}": "put",
            "/api/fair-score/{dataset_id}": "get",
            "/api/{dataset_id}/template/apply-from-paper": "post",
        }
        for path, method in required_operations.items():
            operations = paths.get(path)
            if not isinstance(operations, dict) or method not in operations:
                raise FairPrepareError(
                    f"FAIR-VCG OpenAPI contract is missing {method.upper()} {path}."
                )

        synthetic_csv = (
            b"subject_id,group,activity_count\n"
            b"synthetic-1,control,1\n"
            b"synthetic-2,treatment,2\n"
        )
        uploaded = self._mapping(
            self._request(
                "POST",
                "/api/upload",
                files={"file": ("openscientist-canary.csv", synthetic_csv, "text/csv")},
            ),
            endpoint="/api/upload",
        )
        dataset_id = self._dataset_id(uploaded)
        marker = "OpenScientist FAIR-VCG compatibility canary"
        metadata = self._mapping(
            self._request(
                "PUT",
                f"/api/metadata/{dataset_id}",
                json={
                    "title": marker,
                    "description": "Synthetic deployment readiness record; no study data.",
                    "base_uri": f"urn:openscientist:fair-canary:{dataset_id}",
                    "version": "canary/1",
                },
            ),
            endpoint="/api/metadata/{dataset_id}",
        )
        returned_metadata = metadata.get("metadata")
        if not isinstance(returned_metadata, dict) or returned_metadata.get("title") != marker:
            raise FairPrepareError(
                "FAIR-VCG metadata endpoint did not persist the synthetic marker."
            )

        fair_score = self._mapping(
            self._request("GET", f"/api/fair-score/{dataset_id}"),
            endpoint="/api/fair-score/{dataset_id}",
        )
        self._validate_fair_score(fair_score)

        for framework in FAIR_VCG_REQUIRED_TEMPLATES:
            applied = self._mapping(
                self._request(
                    "POST",
                    f"/api/{dataset_id}/template/apply-from-paper",
                    json={"template_id": framework},
                ),
                endpoint="/api/{dataset_id}/template/apply-from-paper",
            )
            self._validate_template(applied, framework=framework)

        return FairPrepareCompatibilityReport(
            api_version=version,
            dataset_id=dataset_id,
            templates=FAIR_VCG_REQUIRED_TEMPLATES,
        )

    def _assess_bytes(
        self,
        content: bytes,
        *,
        filename: str,
        metadata: dict[str, Any],
        frameworks: tuple[str, ...],
    ) -> list[AssessmentResult]:
        source_hash = hashlib.sha256(content).hexdigest()
        uploaded = self._mapping(
            self._request(
                "POST",
                "/api/upload",
                files={"file": (filename, content, "text/csv")},
            ),
            endpoint="/api/upload",
        )
        dataset_id = self._dataset_id(uploaded)
        self._request("PUT", f"/api/metadata/{dataset_id}", json=metadata)
        fair_score = self._mapping(
            self._request("GET", f"/api/fair-score/{dataset_id}"),
            endpoint="/api/fair-score/{dataset_id}",
        )
        self._validate_fair_score(fair_score)
        results = [self._fair_result(dataset_id, source_hash, fair_score)]
        for framework in frameworks:
            applied = self._mapping(
                self._request(
                    "POST",
                    f"/api/{dataset_id}/template/apply-from-paper",
                    json={"template_id": framework},
                ),
                endpoint="/api/{dataset_id}/template/apply-from-paper",
            )
            self._validate_template(applied, framework=framework)
            results.append(self._template_result(dataset_id, source_hash, framework, applied))
        return results

    @staticmethod
    def _fair_result(
        dataset_id: str,
        source_hash: str,
        score: dict[str, Any],
    ) -> AssessmentResult:
        findings: list[AssessmentFinding] = []
        for dimension in ("findable", "accessible", "interoperable", "reusable"):
            payload = score.get(dimension) or {}
            current = int(payload.get("score", 0))
            maximum = int(payload.get("max_score", 0))
            ratio = current / maximum if maximum else 0.0
            status = "satisfied" if ratio == 1 else "partial" if current > 0 else "missing"
            findings.append(
                AssessmentFinding.model_validate(
                    {
                        "requirement_id": f"FAIR-{dimension[0].upper()}",
                        "status": status,
                        "recommendation": (
                            f"Score {current}/{maximum}. "
                            f"Main weakness: {payload.get('main_weakness', 'not reported')}."
                        ),
                    }
                )
            )
            for criterion, satisfied in (payload.get("criteria") or {}).items():
                findings.append(
                    AssessmentFinding.model_validate(
                        {
                            "requirement_id": f"FAIR-{dimension[0].upper()}:{criterion}",
                            "status": "satisfied" if satisfied else "missing",
                            "missing_fields": [] if satisfied else [criterion],
                        }
                    )
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
        findings: list[AssessmentFinding] = []
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
                AssessmentFinding.model_validate(
                    {
                        "requirement_id": field_id,
                        "status": status,
                        "missing_fields": [field_id] if status == "missing" else [],
                        "recommendation": entry.get("guidance") or entry.get("message"),
                    }
                )
            )
        return AssessmentResult(
            assessment_id=f"fair-vcg-{dataset_id}-{framework}",
            framework=framework,
            framework_version=(f"FAIR-VCG-mentor-template-{FAIR_VCG_PINNED_COMMIT[:12]}"),
            context_hash=source_hash,
            findings=findings,
        )
