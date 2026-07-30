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
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
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
FAIR_VCG_MAX_PAYLOAD_BYTES = 10 * 1024 * 1024


class FairPrepareFailureKind(StrEnum):
    """Stable failure classes exposed to DVC tools and operators."""

    DNS_SERVICE_DISCOVERY = "dns_service_discovery"
    CONNECTION = "connection"
    TIMEOUT = "timeout"
    HTTP = "http"
    CONTRACT = "contract"
    PAYLOAD_VALIDATION = "payload_validation"


class FairPrepareError(RuntimeError):
    """Actionable, machine-readable FAIR/PREPARE integration failure."""

    def __init__(
        self,
        message: str,
        *,
        kind: FairPrepareFailureKind = FairPrepareFailureKind.CONTRACT,
        retryable: bool = False,
        endpoint: str | None = None,
        status_code: int | None = None,
        action: str | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable
        self.endpoint = endpoint
        self.status_code = status_code
        self.action = action

    @property
    def code(self) -> str:
        return f"fair_vcg.{self.kind.value}"

    def to_dict(self) -> dict[str, Any]:
        """Return safe diagnostic fields suitable for MCP/API responses."""

        payload: dict[str, Any] = {
            "error_code": self.code,
            "failure_kind": self.kind.value,
            "retryable": self.retryable,
        }
        if self.endpoint is not None:
            payload["endpoint"] = self.endpoint
        if self.status_code is not None:
            payload["status_code"] = self.status_code
        if self.action is not None:
            payload["action"] = self.action
        return payload


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
            "FAIR_PREPARE_URL must be an absolute http(s) service URL.",
            kind=FairPrepareFailureKind.PAYLOAD_VALIDATION,
            action="Set FAIR_PREPARE_URL to the reachable FAIR-VCG service root.",
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise FairPrepareError(
            "FAIR_PREPARE_URL must not contain credentials, a query, or a fragment.",
            kind=FairPrepareFailureKind.PAYLOAD_VALIDATION,
            action="Use only the FAIR-VCG scheme, hostname, optional port, and base path.",
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


def _payload_error(message: str, *, action: str) -> FairPrepareError:
    return FairPrepareError(
        message,
        kind=FairPrepareFailureKind.PAYLOAD_VALIDATION,
        action=action,
    )


def validate_assessment_payload(
    content: bytes,
    *,
    filename: str,
    metadata: dict[str, Any],
    frameworks: tuple[str, ...],
) -> None:
    """Validate a complete FAIR-VCG assessment request before network I/O."""

    if not isinstance(content, bytes) or not content:
        raise _payload_error(
            "FAIR-VCG assessment payload is empty.",
            action="Provide a non-empty UTF-8 CSV assessment payload.",
        )
    if len(content) > FAIR_VCG_MAX_PAYLOAD_BYTES:
        raise _payload_error(
            "FAIR-VCG assessment payload exceeds the local 10 MiB safety limit.",
            action="Reduce the manifest/context payload before assessment.",
        )
    if not filename or Path(filename).name != filename or not filename.lower().endswith(".csv"):
        raise _payload_error(
            "FAIR-VCG assessment filename must be a plain .csv filename.",
            action="Use a filename without directory components and with a .csv suffix.",
        )
    try:
        decoded = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _payload_error(
            "FAIR-VCG assessment payload is not valid UTF-8.",
            action="Encode the CSV payload as UTF-8 before assessment.",
        ) from exc
    try:
        rows = list(csv.reader(io.StringIO(decoded)))
    except csv.Error as exc:
        raise _payload_error(
            "FAIR-VCG assessment payload is not valid CSV.",
            action="Repair the CSV structure before assessment.",
        ) from exc
    if len(rows) < 2 or not rows[0] or any(not column.strip() for column in rows[0]):
        raise _payload_error(
            "FAIR-VCG assessment CSV must contain a header and at least one data row.",
            action="Populate the local context or bundle manifest before assessment.",
        )
    if len(set(rows[0])) != len(rows[0]):
        raise _payload_error(
            "FAIR-VCG assessment CSV contains duplicate column names.",
            action="Make every CSV header unique before assessment.",
        )

    if not frameworks:
        raise _payload_error(
            "At least one FAIR-VCG assessment framework is required.",
            action="Select one or more supported reporting templates.",
        )
    if len(set(frameworks)) != len(frameworks):
        raise _payload_error(
            "FAIR-VCG assessment frameworks must not contain duplicates.",
            action="Submit each requested reporting template exactly once.",
        )
    unsupported = sorted(set(frameworks) - set(FAIR_VCG_REQUIRED_TEMPLATES))
    if unsupported:
        raise _payload_error(
            f"Unsupported FAIR-VCG assessment framework(s): {', '.join(unsupported)}.",
            action=(
                "Use only the templates verified by this deployment: "
                f"{', '.join(FAIR_VCG_REQUIRED_TEMPLATES)}."
            ),
        )

    required_metadata = ("title", "description", "base_uri", "date_created", "version")
    invalid_metadata = [
        field
        for field in required_metadata
        if not isinstance(metadata.get(field), str) or not metadata[field].strip()
    ]
    if invalid_metadata:
        raise _payload_error(
            "FAIR-VCG metadata is missing non-empty required field(s): "
            f"{', '.join(invalid_metadata)}.",
            action="Complete required metadata locally before assessment.",
        )
    try:
        json.dumps(metadata, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise _payload_error(
            "FAIR-VCG metadata is not JSON-compatible.",
            action="Remove non-finite or non-serializable metadata values.",
        ) from exc


class HttpFairPrepareProvider:
    """FAIR-VCG Mentor REST provider using upload, FAIR score and templates."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout: float = 60.0,
        client: httpx.Client | None = None,
    ) -> None:
        configured_url = base_url or os.getenv("FAIR_PREPARE_URL") or DEFAULT_FAIR_PREPARE_URL
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

        endpoint = f"{method} {path}"
        try:
            response = self.client.request(method, f"{self.base_url}{path}", **kwargs)
        except httpx.TimeoutException as exc:
            detail = redact_sensitive_text(str(exc))
            raise FairPrepareError(
                f"FAIR-VCG {endpoint} timed out: {detail or 'request deadline exceeded'}",
                kind=FairPrepareFailureKind.TIMEOUT,
                retryable=True,
                endpoint=endpoint,
                action=(
                    "Confirm FAIR-VCG is healthy and its dependencies are responsive; "
                    "retry this checkpoint once the service is ready."
                ),
            ) from exc
        except httpx.ConnectError as exc:
            detail = redact_sensitive_text(str(exc))
            dns_failure = self._is_dns_failure(exc)
            kind = (
                FairPrepareFailureKind.DNS_SERVICE_DISCOVERY
                if dns_failure
                else FairPrepareFailureKind.CONNECTION
            )
            action = (
                "Verify FAIR_PREPARE_URL, attach OpenScientist and job containers to "
                "the FAIR runtime network, and confirm the fair-vcg-mentor alias resolves."
                if dns_failure
                else (
                    "Confirm FAIR-VCG is running, listening on the configured port, "
                    "and reachable from the OpenScientist runtime network."
                )
            )
            label = "service discovery failed" if dns_failure else "connection failed"
            raise FairPrepareError(
                f"FAIR-VCG {endpoint} {label}: {detail}",
                kind=kind,
                retryable=True,
                endpoint=endpoint,
                action=action,
            ) from exc
        except httpx.HTTPError as exc:
            detail = redact_sensitive_text(str(exc))
            raise FairPrepareError(
                f"FAIR-VCG {endpoint} connection failed: {detail}",
                kind=FairPrepareFailureKind.CONNECTION,
                retryable=True,
                endpoint=endpoint,
                action="Check FAIR-VCG network reachability and transport configuration.",
            ) from exc
        except Exception as exc:  # noqa: BLE001
            detail = redact_sensitive_text(str(exc))
            raise FairPrepareError(
                f"FAIR-VCG {endpoint} client failure: {detail}",
                kind=FairPrepareFailureKind.CONNECTION,
                retryable=False,
                endpoint=endpoint,
                action="Inspect the configured HTTP client and FAIR-VCG service locator.",
            ) from exc
        if response.status_code >= 400:
            detail = redact_sensitive_text(response.text[:500].strip())
            raise FairPrepareError(
                f"FAIR-VCG {endpoint} failed (HTTP {response.status_code}): {detail}",
                kind=FairPrepareFailureKind.HTTP,
                retryable=(response.status_code in {408, 425, 429} or response.status_code >= 500),
                endpoint=endpoint,
                status_code=response.status_code,
                action=(
                    "Restore FAIR-VCG service health before retrying this checkpoint."
                    if response.status_code >= 500
                    else "Correct the request or service authorization before retrying."
                ),
            )
        try:
            return response.json()
        except ValueError as exc:
            raise FairPrepareError(
                f"FAIR-VCG {endpoint} returned invalid JSON.",
                kind=FairPrepareFailureKind.CONTRACT,
                endpoint=endpoint,
                action="Verify the deployed FAIR-VCG API version and response contract.",
            ) from exc

    @staticmethod
    def _is_dns_failure(exc: BaseException) -> bool:
        current: BaseException | None = exc
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current, socket.gaierror):
                return True
            detail = str(current).lower()
            if any(
                marker in detail
                for marker in (
                    "name or service not known",
                    "nodename nor servname",
                    "getaddrinfo failed",
                    "temporary failure in name resolution",
                )
            ):
                return True
            current = current.__cause__ or current.__context__
        return False

    @staticmethod
    def _contract_error(message: str, *, endpoint: str, action: str | None = None) -> None:
        raise FairPrepareError(
            message,
            kind=FairPrepareFailureKind.CONTRACT,
            endpoint=endpoint,
            action=action or "Verify the pinned FAIR-VCG deployment and API contract.",
        )

    @classmethod
    def _mapping(cls, payload: Any, *, endpoint: str) -> dict[str, Any]:
        if not isinstance(payload, dict):
            cls._contract_error(
                f"FAIR-VCG {endpoint} returned an incompatible response.",
                endpoint=endpoint,
            )
        return payload

    @classmethod
    def _dataset_id(cls, payload: dict[str, Any]) -> str:
        dataset_id = payload.get("dataset_id")
        if (
            not isinstance(dataset_id, str)
            or not dataset_id.strip()
            or len(dataset_id) > 128
            or any(
                character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
                for character in dataset_id
            )
        ):
            cls._contract_error(
                "FAIR-VCG /api/upload response is missing a valid dataset_id.",
                endpoint="POST /api/upload",
                action="Repair the FAIR-VCG upload response before retrying the assessment.",
            )
        return dataset_id

    @staticmethod
    def _validate_fair_score(payload: dict[str, Any]) -> None:
        if isinstance(payload.get("fair_score"), bool) or not isinstance(
            payload.get("fair_score"), (int, float)
        ):
            raise FairPrepareError(
                "FAIR-VCG fair-score response is missing numeric fair_score.",
                kind=FairPrepareFailureKind.CONTRACT,
                endpoint="GET /api/fair-score/{dataset_id}",
                action="Repair the FAIR-VCG fair-score response contract.",
            )
        for dimension in ("findable", "accessible", "interoperable", "reusable"):
            score = payload.get(dimension)
            if not isinstance(score, dict):
                raise FairPrepareError(
                    f"FAIR-VCG fair-score response is missing {dimension}.",
                    kind=FairPrepareFailureKind.CONTRACT,
                    endpoint="GET /api/fair-score/{dataset_id}",
                    action="Repair the FAIR-VCG fair-score response contract.",
                )
            if (
                isinstance(score.get("score"), bool)
                or not isinstance(score.get("score"), (int, float))
                or isinstance(score.get("max_score"), bool)
                or not isinstance(score.get("max_score"), (int, float))
            ):
                raise FairPrepareError(
                    f"FAIR-VCG fair-score {dimension} values are incompatible.",
                    kind=FairPrepareFailureKind.CONTRACT,
                    endpoint="GET /api/fair-score/{dataset_id}",
                    action="Repair the FAIR-VCG fair-score response contract.",
                )
            if not isinstance(score.get("criteria"), dict):
                raise FairPrepareError(
                    f"FAIR-VCG fair-score {dimension}.criteria is incompatible.",
                    kind=FairPrepareFailureKind.CONTRACT,
                    endpoint="GET /api/fair-score/{dataset_id}",
                    action="Repair the FAIR-VCG fair-score response contract.",
                )

    @staticmethod
    def _validate_template(
        payload: dict[str, Any],
        *,
        framework: str,
    ) -> None:
        if payload.get("template_id") != framework:
            raise FairPrepareError(
                f"FAIR-VCG template response did not confirm {framework}.",
                kind=FairPrepareFailureKind.CONTRACT,
                endpoint="POST /api/{dataset_id}/template/apply-from-paper",
                action="Repair the FAIR-VCG template response contract.",
            )
        report = payload.get("conformance_report")
        if not isinstance(report, list) or any(not isinstance(item, dict) for item in report):
            raise FairPrepareError(
                f"FAIR-VCG template {framework} returned an incompatible report.",
                kind=FairPrepareFailureKind.CONTRACT,
                endpoint="POST /api/{dataset_id}/template/apply-from-paper",
                action="Repair the FAIR-VCG template response contract.",
            )

    def preflight(self) -> str:
        """Verify service readiness and the pinned contract without mutating state."""

        openapi = self._mapping(
            self._request("GET", "/openapi.json"),
            endpoint="/openapi.json",
        )
        info = self._mapping(openapi.get("info"), endpoint="/openapi.json info")
        version = info.get("version")
        if version != FAIR_VCG_API_VERSION:
            raise FairPrepareError(
                "FAIR-VCG API version mismatch: "
                f"expected {FAIR_VCG_API_VERSION}, received {version!r}.",
                kind=FairPrepareFailureKind.CONTRACT,
                endpoint="GET /openapi.json",
                action="Deploy the pinned FAIR-VCG revision before running DVC assessment.",
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
                    f"FAIR-VCG OpenAPI contract is missing {method.upper()} {path}.",
                    kind=FairPrepareFailureKind.CONTRACT,
                    endpoint="GET /openapi.json",
                    action="Deploy the pinned FAIR-VCG revision before running DVC assessment.",
                )
        return str(version)

    def check_compatibility(self) -> FairPrepareCompatibilityReport:
        """Exercise the pinned API contract with synthetic, non-sensitive data.

        A report is returned only when the advertised API version and every
        production endpoint/template are compatible. Any outage or mismatch
        raises :class:`FairPrepareError`, allowing deployment to fail closed.
        """

        version = self.preflight()

        synthetic_csv = (
            b"subject_id,group,activity_count\nsynthetic-1,control,1\nsynthetic-2,treatment,2\n"
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
                "FAIR-VCG metadata endpoint did not persist the synthetic marker.",
                kind=FairPrepareFailureKind.CONTRACT,
                endpoint="PUT /api/metadata/{dataset_id}",
                action="Repair the FAIR-VCG metadata persistence contract.",
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
        validate_assessment_payload(
            content,
            filename=filename,
            metadata=metadata,
            frameworks=frameworks,
        )
        self.preflight()
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
        self._mapping(
            self._request("PUT", f"/api/metadata/{dataset_id}", json=metadata),
            endpoint="/api/metadata/{dataset_id}",
        )
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
