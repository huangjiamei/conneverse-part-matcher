"""N-gram text evidence adapter for fitment validation."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping, Optional, Sequence, Set, Tuple

from algorithms.fitment.models import (
    CandidatePart,
    FitmentEngineConfig,
    FitmentEvidence,
    FitmentQuestion,
    clamp_confidence,
)


_NOISE_PATTERNS = (
    r"\b(?:genuine|new|brand\s*new|free\s*shipping|sale|hot\s*sale|oem\s*quality)\b",
)

_ABBREVIATIONS: Mapping[str, str] = {
    "fr": "front",
    "ft": "front",
    "rr": "rear",
    "lh": "left",
    "rh": "right",
    "drv": "driver",
    "pass": "passenger",
    "assy": "assembly",
    "w/": "with",
    "w/o": "without",
}


@dataclass(frozen=True)
class SimilarityBreakdown:
    """Raw overlap metrics for debugging and offline evaluation."""

    coverage_left: float
    coverage_right: float
    jaccard: float
    score: float
    intersection: int
    left_feature_count: int
    right_feature_count: int


def normalize_text(text: Any, *, max_year_range_expansion: int = 35) -> str:
    """Normalize noisy fitment/product text while preserving useful tokens."""

    if text is None:
        return ""
    text = str(text).lower()
    text = (
        text.replace("–", "-")
        .replace("—", "-")
        .replace("‑", "-")
        .replace("‒", "-")
    )

    for pattern in _NOISE_PATTERNS:
        text = re.sub(pattern, " ", text, flags=re.IGNORECASE)

    text = _expand_year_ranges(text, max_year_range_expansion=max_year_range_expansion)
    text = re.sub(r"[^\w\s&\-+/.]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    if not text:
        return ""

    return " ".join(_ABBREVIATIONS.get(word, word) for word in text.split())


def _expand_year_ranges(text: str, *, max_year_range_expansion: int) -> str:
    def repl(match: re.Match[str]) -> str:
        start = int(match.group(1))
        end = int(match.group(2))
        if end < start or end - start > max_year_range_expansion:
            return match.group(0)
        expanded = " ".join(str(year) for year in range(start, end + 1))
        return f"{match.group(0)} {expanded}"

    return re.sub(r"\b(19\d{2}|20\d{2})\s*[-/]\s*(19\d{2}|20\d{2})\b", repl, text)


def ngram_features(text: Any, config: Optional[FitmentEngineConfig] = None) -> Set[str]:
    """Return char-level n-gram and word-level binary features."""

    cfg = config or FitmentEngineConfig()
    normalized = normalize_text(text, max_year_range_expansion=cfg.max_year_range_expansion)
    return _ngram_features_from_normalized(normalized, cfg)


def _ngram_features_from_normalized(normalized: str, config: FitmentEngineConfig) -> Set[str]:
    if not normalized:
        return set()

    compact = normalized.replace(" ", "").replace("&", "")
    features: Set[str] = set()
    min_n, max_n = config.ngram_range
    for n in range(min_n, max_n + 1):
        if len(compact) < n:
            continue
        for idx in range(len(compact) - n + 1):
            features.add(compact[idx : idx + n])

    for word in normalized.split():
        if len(word) >= 2:
            features.add(f"WORD_{word}")
    return features


def ngram_similarity(left: Any, right: Any, config: Optional[FitmentEngineConfig] = None) -> SimilarityBreakdown:
    """Calculate n-gram similarity between two strings."""

    cfg = config or FitmentEngineConfig()
    left_normalized = normalize_text(left, max_year_range_expansion=cfg.max_year_range_expansion)
    right_normalized = normalize_text(right, max_year_range_expansion=cfg.max_year_range_expansion)
    return _ngram_similarity_from_normalized(left_normalized, right_normalized, cfg)


def _ngram_similarity_from_normalized(
    left_normalized: str,
    right_normalized: str,
    config: FitmentEngineConfig,
) -> SimilarityBreakdown:
    left_features = _ngram_features_from_normalized(left_normalized, config)
    right_features = _ngram_features_from_normalized(right_normalized, config)

    if not left_features or not right_features:
        return SimilarityBreakdown(0.0, 0.0, 0.0, 0.0, 0, len(left_features), len(right_features))

    intersection = len(left_features & right_features)
    union = len(left_features | right_features)
    coverage_left = intersection / len(left_features)
    coverage_right = intersection / len(right_features)
    jaccard = intersection / union if union else 0.0
    score = 0.15 * max(coverage_left, coverage_right) + 0.70 * min(coverage_left, coverage_right) + 0.15 * jaccard

    return SimilarityBreakdown(
        coverage_left=coverage_left,
        coverage_right=coverage_right,
        jaccard=jaccard,
        score=score,
        intersection=intersection,
        left_feature_count=len(left_features),
        right_feature_count=len(right_features),
    )


class NgramTextEvidenceAdapter:
    """Produces soft text-similarity evidence for one candidate."""

    method = "ngram_text"

    def __init__(self, config: Optional[FitmentEngineConfig] = None) -> None:
        self.config = config or FitmentEngineConfig()

    def evaluate(self, question: FitmentQuestion, candidate: CandidatePart) -> Tuple[FitmentEvidence, ...]:
        request_part_description = normalize_text(
            question.part_description,
            max_year_range_expansion=self.config.max_year_range_expansion,
        )
        request_position = normalize_text(
            question.position,
            max_year_range_expansion=self.config.max_year_range_expansion,
        )
        request_vehicle = normalize_text(
            question.vehicle,
            max_year_range_expansion=self.config.max_year_range_expansion,
        )

        candidate_title = normalize_text(
            candidate.title,
            max_year_range_expansion=self.config.max_year_range_expansion,
        )
        candidate_vehicle_text = normalize_text(
            candidate.vehicle_text,
            max_year_range_expansion=self.config.max_year_range_expansion,
        )
        candidate_fitment_notes = normalize_text(
            candidate.fitment_notes,
            max_year_range_expansion=self.config.max_year_range_expansion,
        )

        part_name_breakdown = _ngram_similarity_from_normalized(
            request_part_description,
            candidate_title,
            self.config,
        )
        part_name_similarity = self._requirement_containment(part_name_breakdown)

        position_similarity, position_source, position_breakdown = self._best_containment_similarity(
            request_position,
            {
                "fitment_notes": candidate_fitment_notes,
                "title": candidate_title,
            },
            default_score=1.0,
        )
        if request_position:
            part_similarity = 0.80 * part_name_similarity + 0.20 * position_similarity
        else:
            part_similarity = part_name_similarity

        vehicle_similarity, vehicle_source, vehicle_breakdown = self._best_containment_similarity(
            request_vehicle,
            {
                "vehicle_text": candidate_vehicle_text,
                "title": candidate_title,
                "fitment_notes": candidate_fitment_notes,
            },
        )

        weighted_score = clamp_confidence(
            self.config.part_weight * part_similarity
            + self.config.vehicle_weight * vehicle_similarity
        )

        metrics = {
            "part_similarity": part_similarity,
            "vehicle_similarity": vehicle_similarity,
            "weighted_score": weighted_score,
            "part": {
                "score": part_similarity,
                "part_name_score": part_name_similarity,
                "position_score": position_similarity,
                "position_source": position_source,
                "part_name": part_name_breakdown.__dict__,
                "position": position_breakdown.__dict__ if position_breakdown else None,
            },
            "vehicle": {
                "score": vehicle_similarity,
                "source": vehicle_source,
                "breakdown": vehicle_breakdown.__dict__ if vehicle_breakdown else None,
            },
            "normalized_request_part": " ".join(
                x for x in (request_part_description, request_position) if x
            ),
            "normalized_request_part_description": request_part_description,
            "normalized_request_position": request_position,
            "normalized_request_vehicle": request_vehicle,
            "normalized_candidate_title": candidate_title,
            "normalized_candidate_vehicle_text": candidate_vehicle_text,
            "normalized_candidate_fitment_notes": candidate_fitment_notes,
        }

        if not any((request_part_description, request_position, request_vehicle, candidate_title, candidate_vehicle_text, candidate_fitment_notes)):
            effect = "inconclusive"
            reasons = ("no text evidence available",)
        else:
            effect = "soft_support" if weighted_score > 0 else "inconclusive"
            reasons = ("n-gram text evidence computed",)

        return (
            FitmentEvidence(
                method=self.method,
                effect=effect,
                confidence=weighted_score,
                reasons=reasons,
                metrics=metrics,
            ),
        )

    def _best_containment_similarity(
        self,
        requirement: str,
        candidate_fields: Mapping[str, str],
        *,
        default_score: float = 0.0,
    ) -> Tuple[float, str, Optional[SimilarityBreakdown]]:
        if not requirement:
            return default_score, "default", None

        best_score = 0.0
        best_source = ""
        best_breakdown: Optional[SimilarityBreakdown] = None

        for source, value in candidate_fields.items():
            if not value:
                continue
            breakdown = _ngram_similarity_from_normalized(requirement, value, self.config)
            score = self._requirement_containment(breakdown)
            if score > best_score:
                best_score = score
                best_source = source
                best_breakdown = breakdown

        return best_score, best_source, best_breakdown

    @staticmethod
    def _requirement_containment(breakdown: SimilarityBreakdown) -> float:
        return breakdown.coverage_left
