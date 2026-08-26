"""Governed, provider-neutral contracts for scientific agent teams.

Provider-native subagents may translate their output into these plain immutable
contracts.  They cannot directly add canonical claims or workflow transition
requests; those records are produced only by :func:`reduce_proposals`.
"""

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
from openscientist.scientific_team.reducer import reduce_proposals

__all__ = [
    "CanonicalClaim",
    "ClaimProposal",
    "ConflictKind",
    "EvidenceArtifactRef",
    "EvidenceValidation",
    "GovernedScientificState",
    "NarrativeProposal",
    "ProposalConflict",
    "ProposalDecision",
    "ProposalDisposition",
    "ProposalReview",
    "ReductionResult",
    "ReviewDecision",
    "ScientificProposal",
    "ScientificRole",
    "WorkflowTransitionProposal",
    "WorkflowTransitionRequest",
    "reduce_proposals",
]
