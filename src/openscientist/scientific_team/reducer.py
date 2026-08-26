"""Deterministic reducer for governed scientific proposals."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable

from openscientist.scientific_team.models import (
    CanonicalClaim,
    ClaimProposal,
    ConflictKind,
    EvidenceArtifactRef,
    EvidenceValidation,
    GovernedScientificState,
    NarrativeProposal,
    ProposalConflict,
    ProposalDecision,
    ProposalDisposition,
    ProposalReview,
    ReductionResult,
    ReviewDecision,
    ScientificProposal,
    ScientificRole,
    WorkflowTransitionProposal,
    WorkflowTransitionRequest,
)

_CLAIM_AUTHORS = frozenset(
    {
        ScientificRole.DATA_STEWARD,
        ScientificRole.ASSAY_SPECIALIST,
        ScientificRole.STATISTICIAN,
    }
)


def _proposal_id(proposal: ScientificProposal) -> str:
    return proposal.proposal_id


def _valid_artifacts(
    evidence: tuple[EvidenceArtifactRef, ...],
    validations: dict[str, tuple[EvidenceValidation, ...]],
) -> tuple[bool, str]:
    if not evidence:
        return False, "Canonical actions require at least one evidence artifact."
    for artifact in evidence:
        matches = validations.get(artifact.artifact_id, ())
        if not matches:
            return False, f"Evidence artifact {artifact.artifact_id!r} has no validation."
        matching_hashes = tuple(item for item in matches if item.artifact.sha256 == artifact.sha256)
        if not matching_hashes:
            return (
                False,
                f"Evidence artifact {artifact.artifact_id!r} has no matching hash validation.",
            )
        if not all(item.passed for item in matching_hashes):
            return False, f"Evidence artifact {artifact.artifact_id!r} failed validation."
    return True, "Evidence passed independent validation."


def _review_outcome(
    proposal_id: str, reviews: dict[str, tuple[ProposalReview, ...]]
) -> tuple[ProposalDisposition | None, tuple[str, ...], str, ConflictKind | None]:
    proposal_reviews = reviews.get(proposal_id, ())
    authoritative = tuple(
        review
        for review in proposal_reviews
        if review.reviewer_role is ScientificRole.REPRODUCIBILITY_CRITIC
    )
    if not authoritative:
        return (
            ProposalDisposition.HELD_FOR_REVIEW,
            (),
            "An approving reproducibility-critic review is required.",
            None,
        )
    decisions = {review.decision for review in authoritative}
    review_ids = tuple(sorted(review.review_id for review in authoritative))
    if ReviewDecision.REJECT in decisions and ReviewDecision.APPROVE in decisions:
        return (
            ProposalDisposition.CONFLICT,
            review_ids,
            "Authoritative reviews conflict.",
            ConflictKind.CONFLICTING_REVIEWS,
        )
    if ReviewDecision.REJECT in decisions:
        return ProposalDisposition.REJECTED, review_ids, "The proposal was rejected.", None
    if ReviewDecision.REQUEST_CHANGES in decisions:
        return (
            ProposalDisposition.HELD_FOR_REVIEW,
            review_ids,
            "The reproducibility critic requested changes.",
            None,
        )
    return None, review_ids, "The proposal was independently approved.", None


def reduce_proposals(
    state: GovernedScientificState,
    proposals: Iterable[ScientificProposal],
    *,
    validations: Iterable[EvidenceValidation] = (),
    reviews: Iterable[ProposalReview] = (),
) -> ReductionResult:
    """Reduce proposals into canonical records using deterministic policy.

    Specialist output, report prose, provider consensus, and a proposal's own
    claims about evidence validity are never sufficient. Canonical actions need
    an authorized role, matching external validation records, an approving
    reproducibility-critic review, and resolvable claim dependencies.
    """

    ordered = tuple(sorted(proposals, key=_proposal_id))
    decisions: dict[str, ProposalDecision] = {}
    conflicts: list[ProposalConflict] = []

    validation_index_lists: dict[str, list[EvidenceValidation]] = defaultdict(list)
    for validation in sorted(
        validations,
        key=lambda item: (
            item.artifact.artifact_id,
            item.artifact.sha256,
            item.validator_id,
            item.validator_version,
            item.validation_run_id,
        ),
    ):
        validation_index_lists[validation.artifact.artifact_id].append(validation)
    validation_index = {
        artifact_id: tuple(items) for artifact_id, items in validation_index_lists.items()
    }

    review_index_lists: dict[str, list[ProposalReview]] = defaultdict(list)
    for review in sorted(reviews, key=lambda item: (item.proposal_id, item.review_id)):
        review_index_lists[review.proposal_id].append(review)
    review_index = {proposal_id: tuple(items) for proposal_id, items in review_index_lists.items()}

    duplicate_ids = {
        proposal_id
        for proposal_id, count in Counter(item.proposal_id for item in ordered).items()
        if count > 1
    }
    unique: list[ScientificProposal] = []
    for proposal in ordered:
        if proposal.proposal_id in duplicate_ids:
            decisions[proposal.proposal_id] = ProposalDecision(
                proposal.proposal_id,
                ProposalDisposition.CONFLICT,
                "Proposal IDs must be unique within a reduction batch.",
            )
            conflicts.append(
                ProposalConflict(
                    proposal.proposal_id,
                    ConflictKind.DUPLICATE_PROPOSAL_ID,
                    "More than one proposal used this ID.",
                )
            )
        else:
            unique.append(proposal)

    claim_candidates: list[tuple[ClaimProposal, tuple[str, ...]]] = []
    transition_candidates: list[tuple[WorkflowTransitionProposal, tuple[str, ...]]] = []
    for proposal in unique:
        if isinstance(proposal, NarrativeProposal):
            decisions[proposal.proposal_id] = ProposalDecision(
                proposal.proposal_id,
                ProposalDisposition.NON_CANONICAL,
                "Narrative and consensus text may cite canonical claims but cannot create them.",
            )
            continue

        authorized = (
            proposal.author_role in _CLAIM_AUTHORS
            if isinstance(proposal, ClaimProposal)
            else proposal.author_role is ScientificRole.COORDINATOR
        )
        if not authorized:
            decisions[proposal.proposal_id] = ProposalDecision(
                proposal.proposal_id,
                ProposalDisposition.REJECTED,
                f"Role {proposal.author_role.value!r} is not authorized for this proposal type.",
            )
            conflicts.append(
                ProposalConflict(
                    proposal.proposal_id,
                    ConflictKind.UNAUTHORIZED_ROLE,
                    "Role policy rejected the proposal.",
                )
            )
            continue

        evidence_valid, evidence_reason = _valid_artifacts(proposal.evidence, validation_index)
        if not evidence_valid:
            decisions[proposal.proposal_id] = ProposalDecision(
                proposal.proposal_id, ProposalDisposition.REJECTED, evidence_reason
            )
            conflicts.append(
                ProposalConflict(
                    proposal.proposal_id, ConflictKind.INVALID_EVIDENCE, evidence_reason
                )
            )
            continue

        review_disposition, review_ids, review_reason, review_conflict = _review_outcome(
            proposal.proposal_id, review_index
        )
        if review_disposition is not None:
            decisions[proposal.proposal_id] = ProposalDecision(
                proposal.proposal_id, review_disposition, review_reason
            )
            if review_conflict is not None:
                conflicts.append(
                    ProposalConflict(proposal.proposal_id, review_conflict, review_reason)
                )
            continue

        if isinstance(proposal, ClaimProposal):
            claim_candidates.append((proposal, review_ids))
        else:
            transition_candidates.append((proposal, review_ids))

    existing_by_key = {claim.claim_key: claim for claim in state.canonical_claims}
    candidate_by_key: dict[str, list[ClaimProposal]] = defaultdict(list)
    for proposal, _review_ids in claim_candidates:
        candidate_by_key[proposal.claim_key].append(proposal)

    conflicting_claim_proposals: set[str] = set()
    for claim_key, candidates in candidate_by_key.items():
        assertions = {proposal.assertion for proposal in candidates}
        existing = existing_by_key.get(claim_key)
        if existing is not None:
            assertions.add(existing.assertion)
        if len(assertions) <= 1:
            continue
        for proposal in candidates:
            conflicting_claim_proposals.add(proposal.proposal_id)
            reason = f"Claim key {claim_key!r} has incompatible assertions."
            decisions[proposal.proposal_id] = ProposalDecision(
                proposal.proposal_id, ProposalDisposition.CONFLICT, reason
            )
            conflicts.append(
                ProposalConflict(proposal.proposal_id, ConflictKind.CONFLICTING_CLAIM, reason)
            )

    claims = {claim.claim_id: claim for claim in state.canonical_claims}
    claims_by_key = {claim.claim_key: claim for claim in state.canonical_claims}
    pending = [
        item for item in claim_candidates if item[0].proposal_id not in conflicting_claim_proposals
    ]
    while pending:
        progressed = False
        remaining: list[tuple[ClaimProposal, tuple[str, ...]]] = []
        candidate_claim_ids = {proposal.claim_id for proposal, _review_ids in pending}
        for proposal, review_ids in sorted(pending, key=lambda item: item[0].proposal_id):
            existing_id = claims.get(proposal.claim_id)
            existing_key = claims_by_key.get(proposal.claim_key)
            if existing_id is not None or existing_key is not None:
                existing = existing_id or existing_key
                if existing is not None and existing.assertion == proposal.assertion:
                    decisions[proposal.proposal_id] = ProposalDecision(
                        proposal.proposal_id,
                        ProposalDisposition.ALREADY_COMMITTED,
                        "An equivalent canonical claim already exists.",
                    )
                else:
                    reason = "The claim ID or key collides with a different canonical claim."
                    decisions[proposal.proposal_id] = ProposalDecision(
                        proposal.proposal_id, ProposalDisposition.CONFLICT, reason
                    )
                    conflicts.append(
                        ProposalConflict(
                            proposal.proposal_id, ConflictKind.CONFLICTING_CLAIM, reason
                        )
                    )
                progressed = True
                continue
            missing = set(proposal.supporting_claim_ids) - set(claims)
            if missing and missing <= candidate_claim_ids:
                remaining.append((proposal, review_ids))
                continue
            if missing:
                reason = (
                    f"Supporting canonical claims are unavailable: {', '.join(sorted(missing))}."
                )
                decisions[proposal.proposal_id] = ProposalDecision(
                    proposal.proposal_id, ProposalDisposition.CONFLICT, reason
                )
                conflicts.append(
                    ProposalConflict(proposal.proposal_id, ConflictKind.MISSING_CLAIM, reason)
                )
                progressed = True
                continue
            claim = CanonicalClaim(
                claim_id=proposal.claim_id,
                claim_key=proposal.claim_key,
                assertion=proposal.assertion,
                evidence=proposal.evidence,
                supporting_claim_ids=proposal.supporting_claim_ids,
                committed_from_proposal_id=proposal.proposal_id,
                approved_by_review_ids=review_ids,
            )
            claims[claim.claim_id] = claim
            claims_by_key[claim.claim_key] = claim
            decisions[proposal.proposal_id] = ProposalDecision(
                proposal.proposal_id,
                ProposalDisposition.COMMITTED,
                "Authorized proposal, evidence validation, and independent review passed.",
            )
            progressed = True
        if not progressed:
            for proposal, _review_ids in remaining:
                reason = "Supporting claim dependencies contain a cycle."
                decisions[proposal.proposal_id] = ProposalDecision(
                    proposal.proposal_id, ProposalDisposition.CONFLICT, reason
                )
                conflicts.append(
                    ProposalConflict(proposal.proposal_id, ConflictKind.MISSING_CLAIM, reason)
                )
            break
        pending = remaining

    requests = {request.request_id: request for request in state.workflow_transition_requests}
    request_keys = {
        (request.run_id, request.transition): request
        for request in state.workflow_transition_requests
    }
    for proposal, review_ids in sorted(transition_candidates, key=lambda item: item[0].proposal_id):
        missing = set(proposal.supporting_claim_ids) - set(claims)
        if missing:
            reason = f"Supporting canonical claims are unavailable: {', '.join(sorted(missing))}."
            decisions[proposal.proposal_id] = ProposalDecision(
                proposal.proposal_id, ProposalDisposition.CONFLICT, reason
            )
            conflicts.append(
                ProposalConflict(proposal.proposal_id, ConflictKind.MISSING_CLAIM, reason)
            )
            continue
        key = (proposal.run_id, proposal.transition)
        if proposal.proposal_id in requests or key in request_keys:
            decisions[proposal.proposal_id] = ProposalDecision(
                proposal.proposal_id,
                ProposalDisposition.ALREADY_COMMITTED,
                "An equivalent workflow transition request already exists.",
            )
            continue
        request = WorkflowTransitionRequest(
            request_id=proposal.proposal_id,
            run_id=proposal.run_id,
            transition=proposal.transition,
            evidence=proposal.evidence,
            supporting_claim_ids=proposal.supporting_claim_ids,
            approved_by_review_ids=review_ids,
        )
        requests[request.request_id] = request
        request_keys[key] = request
        decisions[proposal.proposal_id] = ProposalDecision(
            proposal.proposal_id,
            ProposalDisposition.TRANSITION_REQUESTED,
            "Authorized request, evidence validation, and independent review passed.",
        )

    next_state = GovernedScientificState(
        canonical_claims=tuple(sorted(claims.values(), key=lambda item: item.claim_id)),
        workflow_transition_requests=tuple(
            sorted(requests.values(), key=lambda item: item.request_id)
        ),
    )
    return ReductionResult(
        state=next_state,
        decisions=tuple(decisions[key] for key in sorted(decisions)),
        conflicts=tuple(
            sorted(conflicts, key=lambda item: (item.proposal_id, item.kind.value, item.details))
        ),
    )
