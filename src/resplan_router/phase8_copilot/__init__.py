"""Architectural Co-Pilot Doorway & Wall Optimizer package (Phase 8)."""

from resplan_router.contracts import (
    CoPilotReport,
    ProposalType,
    StructuralProposal,
)
from resplan_router.phase8_copilot.candidate_generator import (
    PerturbationCandidateGenerator,
)

__all__ = [
    "ProposalType",
    "StructuralProposal",
    "CoPilotReport",
    "PerturbationCandidateGenerator",
]
