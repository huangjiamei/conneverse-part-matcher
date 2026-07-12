"""MPN exact-match evidence adapter."""

from __future__ import annotations

import re
from typing import Any, Optional, Tuple

from algorithms.fitment.models import CandidatePart, FitmentEvidence, FitmentQuestion


def normalize_mpn(value: Any) -> str:
    """Normalize manufacturer part numbers for exact identity checks."""

    return re.sub(r"[^a-z0-9]", "", str(value).lower())


class MpnEvidenceAdapter:
    """Produces hard support when a candidate MPN is explicitly acceptable."""

    method = "mpn_exact"

    def evaluate(self, question: FitmentQuestion, candidate: CandidatePart) -> Tuple[FitmentEvidence, ...]:
        if not candidate.mpn or not question.acceptable_mpn_set:
            return ()

        normalized_candidate = normalize_mpn(candidate.mpn)
        normalized_acceptable = tuple(
            sorted(normalize_mpn(mpn) for mpn in question.acceptable_mpn_set if mpn)
        )
        if normalized_candidate not in set(normalized_acceptable):
            return ()

        return (
            FitmentEvidence(
                method=self.method,
                effect="hard_support",
                confidence=1.0,
                reasons=("candidate MPN is in acceptable_mpn_set",),
                metrics={
                    "normalized_candidate_mpn": normalized_candidate,
                    "normalized_acceptable_mpn_set": normalized_acceptable,
                },
            ),
        )
