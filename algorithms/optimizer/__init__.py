"""
Conneverse Optimizer

Two-stage candidate ranker (V2, 设计见 algorithms/OPTIMIZER.md):
  Stage 1: hard gates knock out ineligible candidates
  Stage 2: soft score ranks survivors by price + speed + quality

Public API:
  optimize(candidates, preset=...) -> {"eligible": [...], "rejected": [...], "meta": {...}}
  Candidate: dataclass, feed to optimize()
  PRESETS: dict[str, Preset] —— Rush / Balanced / Budget / Premium (+ 老名别名)
  build_candidate_from_matcher(candidate_info): matcher pipeline 输出适配
  build_candidate_from_ebay(raw_response): eBay raw response 适配 (直调 eBay 时用)
"""
from .candidate import Candidate
from .gates import GatesConfig, gate_check
from .scoring import (
    ScoringConfig,
    compute_landed_anchor,
    landed_cost,
    price_score,
    quality_breakdown,
    quality_score,
    returns_subscore,
    speed_score,
    warranty_subscore,
)
from .presets import DEFAULT_PRESET, LEGACY_PRESET_ALIASES, PRESETS, Preset, Weights, get_preset
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
    "DEFAULT_PRESET",
    "LEGACY_PRESET_ALIASES",
    "gate_check",
    "quality_score",
    "quality_breakdown",
    "price_score",
    "speed_score",
    "warranty_subscore",
    "returns_subscore",
    "landed_cost",
    "compute_landed_anchor",
    "optimize",
    "get_preset",
    "build_candidate_from_ebay",
    "build_candidate_from_matcher",
]