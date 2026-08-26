from dataclasses import FrozenInstanceError

import pytest

from openscientist.scientific_team import (
    ClaimProposal,
    ConflictKind,
    EvidenceArtifactRef,
    EvidenceValidation,
    GovernedScientificState,
    NarrativeProposal,
    ProposalDisposition,
    ProposalReview,
    ReductionResult,
    ReviewDecision,
    ScientificRole,
    WorkflowTransitionProposal,
    reduce_proposals,
)

ARTIFACT = EvidenceArtifactRef("artifact-1", "a" * 64)
VALIDATION = EvidenceValidation(ARTIFACT, "validator-1", "1.0.0", "validation-run-1", True)


def _review(proposal_id: str, decision: ReviewDecision = ReviewDecision.APPROVE) -> ProposalReview:
    return ProposalReview(
        review_id=f"review-{proposal_id}-{decision.value}",
        proposal_id=proposal_id,
        reviewer_role=ScientificRole.REPRODUCIBILITY_CRITIC,
        decision=decision,
        rationale="Independent reproducibility review.",
    )


def _claim(
    proposal_id: str = "proposal-1",
    *,
    claim_id: str = "claim-1",
    claim_key: str = "outcome:distance",
    assertion: str = "Treatment increased distance travelled.",
    author_role: ScientificRole = ScientificRole.STATISTICIAN,
    supporting_claim_ids: tuple[str, ...] = (),
) -> ClaimProposal:
    return ClaimProposal(
        proposal_id=proposal_id,
        claim_id=claim_id,
        claim_key=claim_key,
        assertion=assertion,
        author_role=author_role,
        evidence=(ARTIFACT,),
        supporting_claim_ids=supporting_claim_ids,
    )


def _disposition(result: ReductionResult, proposal_id: str) -> ProposalDisposition:
    return next(item.disposition for item in result.decisions if item.proposal_id == proposal_id)


def test_fixed_scientific_roles_are_complete() -> None:
    assert {role.value for role in ScientificRole} == {
        "coordinator",
        "data_steward",
        "assay_specialist",
        "statistician",
        "reproducibility_critic",
        "report_synthesizer",
    }


def test_validated_and_independently_reviewed_claim_is_committed() -> None:
    proposal = _claim()

    result = reduce_proposals(
        GovernedScientificState(),
        [proposal],
        validations=[VALIDATION],
        reviews=[_review(proposal.proposal_id)],
    )

    assert _disposition(result, proposal.proposal_id) is ProposalDisposition.COMMITTED
    assert result.state.canonical_claims[0].claim_id == "claim-1"
    assert result.state.canonical_claims[0].committed_from_proposal_id == "proposal-1"
    assert result.state.canonical_claims[0].approved_by_review_ids == ("review-proposal-1-approve",)


@pytest.mark.parametrize("validations", [(), (EvidenceValidation(ARTIFACT, "v", "1", "r", False),)])
def test_unvalidated_specialist_output_cannot_become_truth(
    validations: tuple[EvidenceValidation, ...],
) -> None:
    proposal = _claim(author_role=ScientificRole.ASSAY_SPECIALIST)

    result = reduce_proposals(
        GovernedScientificState(),
        [proposal],
        validations=validations,
        reviews=[_review(proposal.proposal_id)],
    )

    assert not result.state.canonical_claims
    assert _disposition(result, proposal.proposal_id) is ProposalDisposition.REJECTED
    assert result.conflicts[0].kind is ConflictKind.INVALID_EVIDENCE


def test_hash_mismatch_is_not_validated() -> None:
    proposal = _claim()
    other = EvidenceArtifactRef(ARTIFACT.artifact_id, "b" * 64)

    result = reduce_proposals(
        GovernedScientificState(),
        [proposal],
        validations=[EvidenceValidation(other, "validator", "1", "run", True)],
        reviews=[_review(proposal.proposal_id)],
    )

    assert not result.state.canonical_claims
    assert result.conflicts[0].kind is ConflictKind.INVALID_EVIDENCE


def test_claim_without_critic_approval_is_held() -> None:
    proposal = _claim()
    non_authoritative_review = ProposalReview(
        "review-1",
        proposal.proposal_id,
        ScientificRole.COORDINATOR,
        ReviewDecision.APPROVE,
        "Looks acceptable.",
    )

    result = reduce_proposals(
        GovernedScientificState(),
        [proposal],
        validations=[VALIDATION],
        reviews=[non_authoritative_review],
    )

    assert not result.state.canonical_claims
    assert _disposition(result, proposal.proposal_id) is ProposalDisposition.HELD_FOR_REVIEW


def test_conflicting_critic_reviews_are_exposed_not_majority_voted() -> None:
    proposal = _claim()

    result = reduce_proposals(
        GovernedScientificState(),
        [proposal],
        validations=[VALIDATION],
        reviews=[
            _review(proposal.proposal_id, ReviewDecision.APPROVE),
            _review(proposal.proposal_id, ReviewDecision.REJECT),
        ],
    )

    assert not result.state.canonical_claims
    assert _disposition(result, proposal.proposal_id) is ProposalDisposition.CONFLICT
    assert result.conflicts[0].kind is ConflictKind.CONFLICTING_REVIEWS


def test_consensus_narrative_is_always_non_canonical() -> None:
    proposal = NarrativeProposal(
        "consensus-1",
        "All agents agree that the treatment works.",
        ScientificRole.REPORT_SYNTHESIZER,
    )

    result = reduce_proposals(
        GovernedScientificState(),
        [proposal],
        validations=[VALIDATION],
        reviews=[_review(proposal.proposal_id)],
    )

    assert not result.state.canonical_claims
    assert _disposition(result, proposal.proposal_id) is ProposalDisposition.NON_CANONICAL


def test_conflicting_claims_are_not_decided_by_agent_consensus() -> None:
    first = _claim("proposal-a", claim_id="claim-a", assertion="Treatment increased distance.")
    second = _claim("proposal-b", claim_id="claim-b", assertion="Treatment decreased distance.")

    result = reduce_proposals(
        GovernedScientificState(),
        [first, second],
        validations=[VALIDATION],
        reviews=[_review(first.proposal_id), _review(second.proposal_id)],
    )

    assert not result.state.canonical_claims
    assert {_disposition(result, item.proposal_id) for item in (first, second)} == {
        ProposalDisposition.CONFLICT
    }
    assert {conflict.kind for conflict in result.conflicts} == {ConflictKind.CONFLICTING_CLAIM}


def test_only_coordinator_can_request_a_workflow_transition() -> None:
    unauthorized = WorkflowTransitionProposal(
        "transition-specialist",
        "run-1",
        "reporting",
        ScientificRole.ASSAY_SPECIALIST,
        (ARTIFACT,),
    )
    authorized = WorkflowTransitionProposal(
        "transition-coordinator",
        "run-1",
        "reporting",
        ScientificRole.COORDINATOR,
        (ARTIFACT,),
    )

    result = reduce_proposals(
        GovernedScientificState(),
        [unauthorized, authorized],
        validations=[VALIDATION],
        reviews=[_review(unauthorized.proposal_id), _review(authorized.proposal_id)],
    )

    assert len(result.state.workflow_transition_requests) == 1
    assert result.state.workflow_transition_requests[0].request_id == "transition-coordinator"
    assert _disposition(result, authorized.proposal_id) is ProposalDisposition.TRANSITION_REQUESTED
    assert _disposition(result, unauthorized.proposal_id) is ProposalDisposition.REJECTED


def test_claim_dependencies_are_resolved_independently_of_input_order() -> None:
    base = _claim("proposal-z", claim_id="claim-base", claim_key="quality:input")
    dependent = _claim(
        "proposal-a",
        claim_id="claim-dependent",
        claim_key="outcome:derived",
        supporting_claim_ids=(base.claim_id,),
    )
    reviews = [_review(base.proposal_id), _review(dependent.proposal_id)]

    forward = reduce_proposals(
        GovernedScientificState(),
        [base, dependent],
        validations=[VALIDATION],
        reviews=reviews,
    )
    reverse = reduce_proposals(
        GovernedScientificState(),
        [dependent, base],
        validations=[VALIDATION],
        reviews=reversed(reviews),
    )

    assert forward == reverse
    assert {claim.claim_id for claim in forward.state.canonical_claims} == {
        "claim-base",
        "claim-dependent",
    }


def test_contracts_and_canonical_state_are_immutable() -> None:
    proposal = _claim()
    result = reduce_proposals(
        GovernedScientificState(),
        [proposal],
        validations=[VALIDATION],
        reviews=[_review(proposal.proposal_id)],
    )

    with pytest.raises(FrozenInstanceError):
        proposal.assertion = "Changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.state.canonical_claims = ()  # type: ignore[misc]
