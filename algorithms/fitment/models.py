"""Canonical fitment decision model.

This module defines the external fitment framework vocabulary.  The data
structures are intentionally light: the current inputs are still mostly text,
but their meaning is no longer n-gram-specific.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Protocol, Sequence, Tuple


Decision = str  # "match" | "review" | "reject"
EvidenceEffect = str  # "hard_support" | "soft_support" | "soft_contradiction" | "hard_contradiction" | "inconclusive"


@dataclass(frozen=True)
class FitmentEngineConfig:
    """Configuration shared by first-party fitment evidence adapters and policy."""

    ngram_range: Tuple[int, int] = (2, 5)
    match_threshold: float = 0.62
    review_threshold: float = 0.38
    min_vehicle_similarity_for_review: float = 0.25
    min_part_similarity_for_review: float = 0.20
    min_vehicle_similarity_for_match: float = 0.25
    min_part_similarity_for_match: float = 0.35
    part_weight: float = 0.45
    vehicle_weight: float = 0.45
    mpn_weight: float = 0.10
    max_year_range_expansion: int = 35


@dataclass(frozen=True)
class FitmentQuestion:
    """A canonical fitment question for one RO part line.

    The fields remain lightweight while this project is in prototype stage.
    Future catalog/VIN/YMME data can be added through metadata or by deepening
    this model when real integrations justify it.
    """

    vehicle: str
    part_description: str
    question_id: str = ""
    position: str = ""
    acceptable_mpn_set: Sequence[str] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CandidatePart:
    """A supplier or catalog candidate being evaluated for a FitmentQuestion."""

    candidate_id: str
    title: str
    mpn: str = ""
    brand: str = ""
    fitment_notes: str = ""
    vehicle_text: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FitmentEvidence:
    """One adapter-produced signal about a fitment question.

    ``confidence`` is a gating/ranking confidence, not a calibrated
    probability.  ``metrics`` is for debugging and offline evaluation; callers
    should rely on method/effect/confidence/reasons as the stable interface.
    """

    method: str
    effect: EvidenceEffect
    confidence: float
    reasons: Tuple[str, ...] = field(default_factory=tuple)
    metrics: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FitmentDecision:
    """Final engine-owned fitment decision for a single candidate."""

    candidate_id: str
    decision: Decision
    confidence: float
    reasons: Tuple[str, ...]
    evidence: Tuple[FitmentEvidence, ...]
    candidate: CandidatePart
    metrics: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FitmentEvaluation:
    """Batch fitment result for one question and multiple candidates."""

    question_id: str
    decisions: Tuple[FitmentDecision, ...]


class FitmentEvidenceAdapter(Protocol):
    """Per-candidate internal seam for fitment evidence adapters."""

    method: str

    def evaluate(
        self,
        question: FitmentQuestion,
        candidate: CandidatePart,
    ) -> Tuple[FitmentEvidence, ...]:
        """Return evidence for one candidate, without producing final decisions."""


def clamp_confidence(value: float) -> float:
    """Clamp a confidence-like score into [0.0, 1.0]."""

    return max(0.0, min(1.0, float(value)))
