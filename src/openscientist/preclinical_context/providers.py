"""Provider boundary for FAIR, PREPARE, ARRIVE, and related assessments."""

from __future__ import annotations

import hashlib
import json
from typing import Protocol
from uuid import uuid4

from openscientist.preclinical_context.models import (
    AssessmentFinding,
    AssessmentResult,
    AssessmentStatus,
    PreclinicalStudyContext,
)


class PreclinicalAssessmentProvider(Protocol):
    """Authoritative rules engine used before and after scientific analysis."""

    def assess_context(
        self,
        context: PreclinicalStudyContext,
        *,
        frameworks: tuple[str, ...],
    ) -> list[AssessmentResult]: ...

    def assess_bundle(
        self,
        bundle_manifest: dict[str, object],
        *,
        frameworks: tuple[str, ...],
    ) -> list[AssessmentResult]: ...


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


class StubPreclinicalAssessmentProvider:
    """Deterministic development provider; never claims framework compliance.

    This provider makes integration and report rendering testable before a
    concrete FAIR-PREPARE package or HTTP adapter is selected. Every finding is
    explicitly partial or missing and must not be presented as an authoritative
    FAIR/PREPARE/ARRIVE assessment.
    """

    provider_version = "stub/0.1"

    def assess_context(
        self,
        context: PreclinicalStudyContext,
        *,
        frameworks: tuple[str, ...],
    ) -> list[AssessmentResult]:
        context_payload = context.model_dump(mode="json")
        context_hash = _canonical_hash(context_payload)
        missing: list[str] = []
        if context.design.experimental_unit.status.value == "unknown":
            missing.append("design.experimental_unit")
        if context.objective.status.value == "unknown":
            missing.append("objective")

        return [
            AssessmentResult(
                assessment_id=str(uuid4()),
                framework=framework,
                framework_version=self.provider_version,
                context_hash=context_hash,
                findings=[
                    AssessmentFinding(
                        requirement_id=f"{framework.lower()}-stub-readiness",
                        status=(AssessmentStatus.MISSING if missing else AssessmentStatus.PARTIAL),
                        missing_fields=missing,
                        blocks=(
                            ["inferential_analysis"]
                            if "design.experimental_unit" in missing
                            else []
                        ),
                        recommendation=(
                            "Connect an authoritative FAIR-PREPARE provider before reporting "
                            "framework satisfaction."
                        ),
                    )
                ],
            )
            for framework in frameworks
        ]

    def assess_bundle(
        self,
        bundle_manifest: dict[str, object],
        *,
        frameworks: tuple[str, ...],
    ) -> list[AssessmentResult]:
        context_hash = _canonical_hash(bundle_manifest)
        return [
            AssessmentResult(
                assessment_id=str(uuid4()),
                framework=framework,
                framework_version=self.provider_version,
                context_hash=context_hash,
                findings=[
                    AssessmentFinding(
                        requirement_id=f"{framework.lower()}-stub-bundle",
                        status=AssessmentStatus.PARTIAL,
                        recommendation=(
                            "This is a structural integration check, not an authoritative "
                            "framework assessment."
                        ),
                    )
                ],
            )
            for framework in frameworks
        ]
