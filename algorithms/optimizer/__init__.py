"""
Conneverse Optimizer

Two-stage candidate ranker:
  Stage 1: hard gates knock out ineligible candidates
  Stage 2: soft score ranks survivors by price + quality

Public API:
  optimize(candidates, preset=...) -> {"eligible": [...], "rejected": [...], "meta": {...}}
  Candidate: dataclass, feed to optimize()
  PRESETS: dict[str, Preset]
  build_candidate_from_matcher(candidate_info): matcher pipeline 输出适配
  build_candidate_from_ebay(raw_response): eBay raw response 适配 (直调 eBay 时用)
"""
from .candidate import Candidate
from .gates import GatesConfig, gate_check
from .scoring import ScoringConfig, price_score, quality_score
from .presets import PRESETS, Preset, Weights, get_preset
from .optimizer import optimize
from .ebay_adapter import build_candidate_from_ebay
from .matcher_adapter import build_candidate_from_matcher

__all__ = [
    "Candidate",
    "GatesConfig",
    "ScoringConfig",
    "Weights",
    "Preset",
    "PRESETS",
    "gate_check",
    "quality_score",
    "price_score",
    "optimize",
    "get_preset",
    "build_candidate_from_ebay",
    "build_candidate_from_matcher",
]