"""Fitment decision engine and deterministic policy."""

from __future__ import annotations

from typing import Iterable, List, Optional, Sequence, Tuple

from algorithms.fitment.adapters.conflicts import FitmentConflictEvidenceAdapter
from algorithms.fitment.adapters.mpn import MpnEvidenceAdapter
from algorithms.fitment.adapters.ngram_text import NgramTextEvidenceAdapter
from algorithms.fitment.models import (
    CandidatePart,
    Decision,
    FitmentDecision,
    FitmentEngineConfig,
    FitmentEvaluation,
    FitmentEvidence,
    FitmentEvidenceAdapter,
    FitmentQuestion,
    clamp_confidence,
)


class FitmentDecisionEngine:
    """External seam for fitment validation.

    The engine owns final match/review/reject decisions.  Adapters only return
    evidence, keeping the final policy centralized and explainable.
    """

    def __init__(
        self,
        adapters: Optional[Sequence[FitmentEvidenceAdapter]] = None,
        config: Optional[FitmentEngineConfig] = None,
    ) -> None:
        self.config = config or FitmentEngineConfig()
        self._validate_weights()
        self.adapters = tuple(adapters) if adapters is not None else self.default_adapters(self.config)

    @staticmethod
    def default_adapters(config: Optional[FitmentEngineConfig] = None) -> Tuple[FitmentEvidenceAdapter, ...]:
        cfg = config or FitmentEngineConfig()
        return (
            MpnEvidenceAdapter(),
            NgramTextEvidenceAdapter(cfg),
            FitmentConflictEvidenceAdapter(cfg),
        )

    def evaluate(self, question: FitmentQuestion, candidates: Iterable[CandidatePart]) -> FitmentEvaluation:
        """Evaluate and sort candidates by fitment suitability."""

        decisions = tuple(self._evaluate_candidate(question, candidate) for candidate in candidates)
        decision_order = {"match": 0, "review": 1, "reject": 2}
        ranked = tuple(
            sorted(
                decisions,
                key=lambda d: (decision_order[d.decision], -d.confidence, d.candidate_id),
            )
        )
        return FitmentEvaluation(question_id=question.question_id, decisions=ranked)

    def evaluate_one(self, question: FitmentQuestion, candidate: CandidatePart) -> FitmentDecision:
        """Convenience method for tests and debugging."""

        return self._evaluate_candidate(question, candidate)

    def _evaluate_candidate(self, question: FitmentQuestion, candidate: CandidatePart) -> FitmentDecision:
        evidence: List[FitmentEvidence] = []
        for adapter in self.adapters:
            evidence.extend(adapter.evaluate(question, candidate))

        decision, confidence, reasons, metrics = self._decide(tuple(evidence))
        return FitmentDecision(
            candidate_id=candidate.candidate_id,
            decision=decision,
            confidence=confidence,
            reasons=tuple(reasons),
            evidence=tuple(evidence),
            candidate=candidate,
            metrics=metrics,
        )

    def _decide(self, evidence: Tuple[FitmentEvidence, ...]) -> Tuple[Decision, float, List[str], dict]:
        hard_supports = tuple(e for e in evidence if e.effect == "hard_support")
        hard_contradictions = tuple(e for e in evidence if e.effect == "hard_contradiction")
        ngram = next((e for e in evidence if e.method == "ngram_text"), None)

        part_similarity = float(ngram.metrics.get("part_similarity", 0.0)) if ngram else 0.0
        vehicle_similarity = float(ngram.metrics.get("vehicle_similarity", 0.0)) if ngram else 0.0
        soft_score = float(ngram.metrics.get("weighted_score", 0.0)) if ngram else 0.0

        reasons: List[str] = []
        for item in evidence:
            reasons.extend(item.reasons)

        if part_similarity < self.config.min_part_similarity_for_match:
            reasons.append("weak part-name similarity")
        if vehicle_similarity < self.config.min_vehicle_similarity_for_match:
            reasons.append("weak vehicle/fitment evidence")

        metrics = {
            "part_similarity": part_similarity,
            "vehicle_similarity": vehicle_similarity,
            "soft_score": soft_score,
            "evidence_count": len(evidence),
            "hard_support_methods": tuple(e.method for e in hard_supports),
            "hard_contradiction_methods": tuple(e.method for e in hard_contradictions),
        }

        if hard_contradictions:
            confidence = self._max_confidence((*hard_supports, *hard_contradictions), default=soft_score)
            if hard_supports:
                reasons.append("hard support conflicts with hard contradiction; route to review")
                return "review", confidence, reasons, metrics
            reasons.append("explicit fitment conflicts reject candidate")
            return "reject", confidence, reasons, metrics

        if hard_supports:
            confidence = self._max_confidence(hard_supports, default=1.0)
            reasons.append("hard fitment support allows deterministic acceptance")
            return "match", confidence, reasons, metrics

        if (
            soft_score >= self.config.match_threshold
            and part_similarity >= self.config.min_part_similarity_for_match
            and vehicle_similarity >= self.config.min_vehicle_similarity_for_match
        ):
            reasons.append("soft part and vehicle evidence exceed match thresholds")
            return "match", clamp_confidence(soft_score), reasons, metrics

        if (
            soft_score >= self.config.review_threshold
            and part_similarity >= self.config.min_part_similarity_for_review
            and vehicle_similarity >= self.config.min_vehicle_similarity_for_review
        ):
            reasons.append("partial fitment evidence; route to human/catalog confirmation")
            return "review", clamp_confidence(soft_score), reasons, metrics

        reasons.append("fitment evidence below review threshold")
        return "reject", clamp_confidence(soft_score), reasons, metrics

    @staticmethod
    def _max_confidence(evidence: Sequence[FitmentEvidence], *, default: float) -> float:
        if not evidence:
            return clamp_confidence(default)
        return clamp_confidence(max(e.confidence for e in evidence))

    def _validate_weights(self) -> None:
        total = self.config.part_weight + self.config.vehicle_weight + self.config.mpn_weight
        if total <= 0:
            raise ValueError("fitment matcher weights must sum to a positive value")
        if abs(total - 1.0) > 1e-9:
            raise ValueError("fitment matcher weights must sum to 1.0")
