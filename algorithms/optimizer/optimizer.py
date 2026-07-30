"""
优化器主入口 (V2, 三层结构见 OPTIMIZER.md)。

用法:
    from optimizer import optimize, PRESETS

    result = optimize(candidates, preset="Balanced")
    for r in result["eligible"]:
        print(r["candidate"].title, r["total"])

或直接传自定义 gates + weights + scoring 配置。

向后兼容:
  - 老 preset 名 (sameDayJob / costFirst / qualityFirst / scheduled) 仍可用, 见 presets.py
  - 只传 weights_price + weights_quality 的老调用 → weights_speed 默认 0, 退化成两维
  - meta 里 min_eligible_price 保留为 landed_anchor 的别名
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from .candidate import Candidate
from .gates import GatesConfig, gate_check
from .presets import Preset, get_preset
from .scoring import (
    ScoringConfig,
    compute_landed_anchor,
    landed_cost,
    price_score,
    quality_score,
    speed_score,
)


def optimize(
    candidates: List[Candidate],
    preset: Optional[Union[str, Preset]] = None,
    gates: Optional[GatesConfig] = None,
    scoring: Optional[ScoringConfig] = None,
    weights_price: Optional[float] = None,
    weights_quality: Optional[float] = None,
    weights_speed: Optional[float] = None,
) -> Dict[str, Any]:
    """
    对候选列表跑一遍 gate + 打分 + 排序。

    参数:
        candidates: 待优化候选列表
        preset: 场景名 ("Balanced" / 老名 "sameDayJob" 等) 或 Preset 实例
        gates / scoring / weights_*: 显式覆盖 preset 的对应部分

        weights_speed 不传且没有 preset 时默认 0 —— 老调用方只给 price/quality,
        这样退化成 V1 的两维加权, 不会因为凭空多出速度维而排序漂移。

    返回:
        {
            "eligible": [
                {
                    "candidate": Candidate,
                    "rank": int,
                    "price_score": float,
                    "speed_score": float,
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
                "landed_anchor": float or None,
                "min_eligible_price": float or None,   # landed_anchor 的老名别名
                "weights": {"price": float, "speed": float, "quality": float},
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
        w_speed = weights_speed if weights_speed is not None else preset.weights.speed
    else:
        gates_cfg = gates or GatesConfig()
        scoring_cfg = scoring or ScoringConfig()
        w_price = weights_price if weights_price is not None else 50.0
        w_quality = weights_quality if weights_quality is not None else 50.0
        # 老调用方没有速度维: 默认 0, 保持 V1 行为
        w_speed = weights_speed if weights_speed is not None else 0.0

    # 权重归一化
    total_w = w_price + w_speed + w_quality
    if total_w <= 0:
        total_w = 1.0
    wp = w_price / total_w
    ws = w_speed / total_w
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
        "landed_anchor": None,
        "min_eligible_price": None,   # 老字段别名, 跟 landed_anchor 同值
        "weights": {"price": wp, "speed": ws, "quality": wq},
    }

    # ---------- Stage 2: scoring ----------
    if eligible:
        # 锚点只看合格候选: 被 gate 掉的低价件不该把价格分踩下去
        landeds = [landed_cost(e["candidate"]) for e in eligible]
        anchor = compute_landed_anchor(landeds, scoring_cfg.anchor_outlier_ratio)
        meta["landed_anchor"] = anchor
        meta["min_eligible_price"] = anchor

        for e, landed in zip(eligible, landeds):
            c = e["candidate"]
            ps = price_score(landed, anchor)
            ss = speed_score(c.delivery_days_max, scoring_cfg.d_fast, scoring_cfg.d_slow)
            qs = quality_score(c, scoring_cfg)
            e["landed"] = landed
            e["price_score"] = ps
            e["speed_score"] = ss
            e["quality_score"] = qs
            e["total"] = wp * ps + ws * ss + wq * qs

        eligible.sort(key=lambda e: e["total"], reverse=True)
        for i, e in enumerate(eligible, start=1):
            e["rank"] = i

    return {"eligible": eligible, "rejected": rejected, "meta": meta}
