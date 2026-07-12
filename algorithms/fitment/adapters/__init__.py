"""First-party fitment evidence adapters."""

from .conflicts import FitmentConflictEvidenceAdapter
from .mpn import MpnEvidenceAdapter, normalize_mpn
from .ngram_text import (
    NgramTextEvidenceAdapter,
    SimilarityBreakdown,
    ngram_features,
    ngram_similarity,
    normalize_text,
)

__all__ = [
    "FitmentConflictEvidenceAdapter",
    "MpnEvidenceAdapter",
    "NgramTextEvidenceAdapter",
    "SimilarityBreakdown",
    "ngram_features",
    "ngram_similarity",
    "normalize_mpn",
    "normalize_text",
]
