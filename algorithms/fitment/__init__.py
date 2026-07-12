"""Canonical fitment validation framework."""

from .engine import FitmentDecisionEngine
from .models import (
    CandidatePart,
    Decision,
    EvidenceEffect,
    FitmentDecision,
    FitmentEngineConfig,
    FitmentEvaluation,
    FitmentEvidence,
    FitmentEvidenceAdapter,
    FitmentQuestion,
)

__all__ = [
    "CandidatePart",
    "Decision",
    "EvidenceEffect",
    "FitmentDecision",
    "FitmentDecisionEngine",
    "FitmentEngineConfig",
    "FitmentEvaluation",
    "FitmentEvidence",
    "FitmentEvidenceAdapter",
    "FitmentQuestion",
]
