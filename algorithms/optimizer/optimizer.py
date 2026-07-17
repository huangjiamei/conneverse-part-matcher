"""
优化器主入口.

用法:
    from optimizer import optimize, PRESETS

    result = optimize(candidates, preset="sameDayJob")
    for r in result["eligible"]:
        print(r["candidate"].title, r["total"])

或直接传自定义 gates + weights + scoring 配置.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, List, Optional, Union

from .candidate import Candidate
from .gates import GatesConfig, gate_check
from .presets import Preset, get_preset
from .scoring import ScoringConfig, price_score, quality_score


def optimize(
    candidates: List[Candidate],
    preset: Optional[Union[str, Preset]] = None,
    gates: Optional[GatesConfig] = None,
    scoring: Optional[ScoringConfig] = None,
    weights_price: Optional[float] = None,
    weights_quality: Optional[float] = None,
) -> Dict[str, Any]:
    """
    对候选列表跑一遍 gate + 打分 + 排序.

    参数:
        candidates: 待优化候选列表
        preset: 场景名 ("sameDayJob" 等) 或 Preset 实例. 如果给了, 内部字段可以被覆盖.
        gates / scoring / weights_*: 显式覆盖 preset 的对应部分.

    返回:
        {
            "eligible": [
                {
                    "candidate": Candidate,
                    "rank": int,
                    "price_score": float,
                    "quality_score": float,
                    "total": float,
                },
                ...  # 已按 total 降序
            ],
            "rejected": [
                {"candidate": Candidate, "reason": str},
                ...
            ],
            "meta": {
                "preset_used": str or None,
                "total_input": int,
                "total_eligible": int,
                "total_rejected": int,
                "min_eligible_price": float or None,
            },
        }
    """
    # 解析 preset
    if isinstance(preset, str):
        preset = get_preset(preset)

    # 组装最终配置: preset 兜底, 显式参数覆盖
    if preset is not None:
        gates_cfg = gates or preset.gates
        scoring_cfg = scoring or preset.scoring
        w_price = weights_price if weights_price is not None else preset.weights.price
        w_quality = weights_quality if weights_quality is not None else preset.weights.quality
    else:
        gates_cfg = gates or GatesConfig()
        scoring_cfg = scoring or ScoringConfig()
        w_price = weights_price if weights_price is not None else 50.0
        w_quality = weights_quality if weights_quality is not None else 50.0

    # 权重归一化
    total_w = w_price + w_quality
    if total_w <= 0:
        total_w = 1.0
    wp = w_price / total_w
    wq = w_quality / total_w

    # ---------- Stage 1: gates ----------
    eligible: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for c in candidates:
        reason = gate_check(c, gates_cfg)
        if reason:
            rejected.append({"candidate": c, "reason": reason})
        else:
            eligible.append({"candidate": c})

    meta: Dict[str, Any] = {
        "preset_used": preset.name if preset else None,
        "total_input": len(candidates),
        "total_eligible": len(eligible),
        "total_rejected": len(rejected),
        "min_eligible_price": None,
    }

    # ---------- Stage 2: scoring ----------
    if eligible:
        min_price = min(e["candidate"].price for e in eligible if e["candidate"].price > 0)
        meta["min_eligible_price"] = min_price

        for e in eligible:
            c = e["candidate"]
            ps = price_score(c.price, min_price)
            qs = quality_score(c, scoring_cfg)
            e["price_score"] = ps
            e["quality_score"] = qs
            e["total"] = wp * ps + wq * qs

        eligible.sort(key=lambda e: e["total"], reverse=True)
        for i, e in enumerate(eligible, start=1):
            e["rank"] = i

    return {"eligible": eligible, "rejected": rejected, "meta": meta}