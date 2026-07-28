"""HTTP integration with Neuronautix/FAIR-VCG-mentor.

OpenScientist treats FAIR-VCG Mentor as the authoritative FAIR/reporting-template
assessment service. This module translates versioned OpenScientist contracts into
its documented REST API and validates the returned structures.
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
from pathlib import Path
from typing import Any, Protocol

import httpx

from openscientist.preclinical_context.models import AssessmentResult, PreclinicalStudyContext

DEFAULT_FAIR_PREPARE_URL = "http://fair-vcg-mentor:8000"
FAIR_VCG_API_VERSION = "1.0.0"
FAIR_VCG_TEMPLATE_REVISION = "main"


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
            frameworks=frameworks,
        )

    def assess_bundle(
        self,
        bundle_dir: Path,
        *,
        frameworks: tuple[str, ...] = ("arrive-v2", "mnms-v1"),
    ) -> list[AssessmentResult]:
        return self._assess_bytes(
            bundle_manifest_to_csv(Path(bundle_dir)),
            filename="bundle-manifest.csv",
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
        frameworks: tuple[str, ...],
    ) -> list[AssessmentResult]:
        source_hash = hashlib.sha256(content).hexdigest()
        uploaded = self._request(
            "POST",
            "/api/upload",
            files={"file": (filename, content, "text/csv")},
        )
        dataset_id = str(uploaded["dataset_id"])
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
            framework_version=f"FAIR-VCG-mentor-template-{FAIR_VCG_TEMPLATE_REVISION}",
            context_hash=source_hash,
            findings=findings,
        )
