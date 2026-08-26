"""Immutable contracts for governed scientific-team collaboration."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeAlias

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _require_text(name: str, value: str) -> None:
    if not value or value != value.strip():
        raise ValueError(f"{name} must be non-empty and have no surrounding whitespace")


def _require_unique(name: str, values: tuple[str, ...]) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{name} must not contain duplicates")


class ScientificRole(StrEnum):
    """Fixed roles in a governed scientific team."""

    COORDINATOR = "coordinator"
    DATA_STEWARD = "data_steward"
    ASSAY_SPECIALIST = "assay_specialist"
    STATISTICIAN = "statistician"
    REPRODUCIBILITY_CRITIC = "reproducibility_critic"
    REPORT_SYNTHESIZER = "report_synthesizer"


class ReviewDecision(StrEnum):
    """An independent critic's review of one proposal."""

    APPROVE = "approve"
    REQUEST_CHANGES = "request_changes"
    REJECT = "reject"


class ProposalDisposition(StrEnum):
    """Reducer outcome for one proposal."""

    COMMITTED = "committed"
    TRANSITION_REQUESTED = "transition_requested"
    ALREADY_COMMITTED = "already_committed"
    HELD_FOR_REVIEW = "held_for_review"
    REJECTED = "rejected"
    CONFLICT = "conflict"
    NON_CANONICAL = "non_canonical"


class ConflictKind(StrEnum):
    """Machine-readable reasons why a proposal cannot be reduced."""

    DUPLICATE_PROPOSAL_ID = "duplicate_proposal_id"
    CONFLICTING_REVIEWS = "conflicting_reviews"
    CONFLICTING_CLAIM = "conflicting_claim"
    INVALID_EVIDENCE = "invalid_evidence"
    MISSING_CLAIM = "missing_claim"
    UNAUTHORIZED_ROLE = "unauthorized_role"


@dataclass(frozen=True, slots=True)
class EvidenceArtifactRef:
    """Content-addressed reference to evidence; never a validation assertion."""

    artifact_id: str
    sha256: str

    def __post_init__(self) -> None:
        _require_text("artifact_id", self.artifact_id)
        if not _SHA256.fullmatch(self.sha256):
            raise ValueError("sha256 must be a lowercase 64-character hexadecimal digest")


@dataclass(frozen=True, slots=True)
class EvidenceValidation:
    """Independent validator result supplied to the reducer.

    A proposal cannot validate its own evidence.  The reducer receives these
    records separately from the proposal-producing agent.
    """

    artifact: EvidenceArtifactRef
    validator_id: str
    validator_version: str
    validation_run_id: str
    passed: bool
    details: str = ""

    def __post_init__(self) -> None:
        _require_text("validator_id", self.validator_id)
        _require_text("validator_version", self.validator_version)
        _require_text("validation_run_id", self.validation_run_id)


@dataclass(frozen=True, slots=True)
class ClaimProposal:
    """Proposal for a scientific claim; it is not itself scientific truth."""

    proposal_id: str
    claim_id: str
    claim_key: str
    assertion: str
    author_role: ScientificRole
    evidence: tuple[EvidenceArtifactRef, ...]
    supporting_claim_ids: tuple[str, ...] = ()
    source_task_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("proposal_id", "claim_id", "claim_key", "assertion"):
            _require_text(name, getattr(self, name))
        _require_unique("supporting_claim_ids", self.supporting_claim_ids)
        _require_unique("evidence artifact IDs", tuple(item.artifact_id for item in self.evidence))


@dataclass(frozen=True, slots=True)
class WorkflowTransitionProposal:
    """Proposal to request, but not directly apply, a workflow transition."""

    proposal_id: str
    run_id: str
    transition: str
    author_role: ScientificRole
    evidence: tuple[EvidenceArtifactRef, ...]
    supporting_claim_ids: tuple[str, ...] = ()
    source_task_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("proposal_id", "run_id", "transition"):
            _require_text(name, getattr(self, name))
        _require_unique("supporting_claim_ids", self.supporting_claim_ids)
        _require_unique("evidence artifact IDs", tuple(item.artifact_id for item in self.evidence))


@dataclass(frozen=True, slots=True)
class NarrativeProposal:
    """Interpretive or consensus text that is categorically non-canonical."""

    proposal_id: str
    text: str
    author_role: ScientificRole
    referenced_claim_ids: tuple[str, ...] = ()
    source_task_id: str | None = None

    def __post_init__(self) -> None:
        _require_text("proposal_id", self.proposal_id)
        _require_text("text", self.text)
        _require_unique("referenced_claim_ids", self.referenced_claim_ids)


ScientificProposal: TypeAlias = ClaimProposal | WorkflowTransitionProposal | NarrativeProposal


@dataclass(frozen=True, slots=True)
class ProposalReview:
    """Independent review. Only the reproducibility critic is authoritative."""

    review_id: str
    proposal_id: str
    reviewer_role: ScientificRole
    decision: ReviewDecision
    rationale: str
    source_task_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("review_id", "proposal_id", "rationale"):
            _require_text(name, getattr(self, name))


@dataclass(frozen=True, slots=True)
class CanonicalClaim:
    """A reducer-committed claim backed by independently validated evidence."""

    claim_id: str
    claim_key: str
    assertion: str
    evidence: tuple[EvidenceArtifactRef, ...]
    supporting_claim_ids: tuple[str, ...]
    committed_from_proposal_id: str
    approved_by_review_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WorkflowTransitionRequest:
    """A reducer-approved request for a workflow owner to apply."""

    request_id: str
    run_id: str
    transition: str
    evidence: tuple[EvidenceArtifactRef, ...]
    supporting_claim_ids: tuple[str, ...]
    approved_by_review_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GovernedScientificState:
    """Immutable canonical state. Use ``reduce_proposals`` to advance it."""

    canonical_claims: tuple[CanonicalClaim, ...] = ()
    workflow_transition_requests: tuple[WorkflowTransitionRequest, ...] = ()


@dataclass(frozen=True, slots=True)
class ProposalConflict:
    proposal_id: str
    kind: ConflictKind
    details: str


@dataclass(frozen=True, slots=True)
class ProposalDecision:
    proposal_id: str
    disposition: ProposalDisposition
    reason: str


@dataclass(frozen=True, slots=True)
class ReductionResult:
    """Deterministic reducer output including all reviewable dispositions."""

    state: GovernedScientificState
    decisions: tuple[ProposalDecision, ...]
    conflicts: tuple[ProposalConflict, ...]
