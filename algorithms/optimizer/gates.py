"""
硬门槛 (Hard Gates).

每个 gate 独立判断, 返回 None 表示放行, 返回字符串表示拒绝原因.
最终 gate_check() 走一遍所有 gates, 返回第一个拒绝原因或 None.

设计原则:
  - 缺信号时放行 (None-tolerant), 不因为数据不全就 knock 掉候选
  - 拒绝原因是机器可读字符串, 上层可以统计 funnel
  - Gate 配置 (阈值) 通过 GatesConfig 传, 不 hardcode
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .candidate import Candidate


@dataclass
class GatesConfig:
    # Condition gate
    allow_used: bool = False              # 是否允许 Used

    # Stock gate
    require_in_stock: bool = True

    # 卖家门槛
    min_seller_feedback_pct: float = 95.0     # < 95% 掉
    min_seller_feedback_count: int = 50       # 评价太少的卖家不要

    # 地理门槛 (未来 preset 可以覆盖: 应急场景只要 US, 计划采购放开)
    require_domestic: bool = False            # True 时只留 country == "US"

    # 以下 gate 在数据不全时自动 skip, 有数据才生效
    max_fitment_complaint_rate: float = 0.15  # 只在有 sample 时生效
    min_fitment_sample: int = 8               # sample < 此值不启用 fitment gate


def _gate_condition(c: Candidate, cfg: GatesConfig) -> Optional[str]:
    if not c.condition:
        return None  # 缺字段放行
    if c.condition == "New":
        return None
    if c.condition == "New other (see details)":
        return None  # 视为可接受, 展示时给标记
    if c.condition == "Used" and not cfg.allow_used:
        return "condition:used"
    return None


def _gate_stock(c: Candidate, cfg: GatesConfig) -> Optional[str]:
    if not cfg.require_in_stock:
        return None
    if c.availability_status != "IN_STOCK":
        return f"stock:{c.availability_status.lower()}"
    return None


def _gate_seller(c: Candidate, cfg: GatesConfig) -> Optional[str]:
    if c.seller_feedback_pct and c.seller_feedback_pct < cfg.min_seller_feedback_pct:
        return f"seller_feedback:{c.seller_feedback_pct:.1f}%"
    if c.seller_feedback_count and c.seller_feedback_count < cfg.min_seller_feedback_count:
        return f"seller_count:{c.seller_feedback_count}"
    return None


def _gate_country(c: Candidate, cfg: GatesConfig) -> Optional[str]:
    if not cfg.require_domestic:
        return None
    if c.country and c.country != "US":
        return f"country:{c.country}"
    return None


def _gate_fitment(c: Candidate, cfg: GatesConfig) -> Optional[str]:
    # 只在有实际样本且样本 >= 阈值时才启用
    if c.fitment_complaint_rate is None or c.fitment_review_sample is None:
        return None  # 没数据就不 gate
    if c.fitment_review_sample < cfg.min_fitment_sample:
        return None  # 样本太小不判
    if c.fitment_complaint_rate > cfg.max_fitment_complaint_rate:
        return f"fitment_risk:{c.fitment_complaint_rate:.0%}"
    return None


# 顺序: 便宜 gate 在前, 贵 gate 在后, 早失败快
_GATES = [
    _gate_condition,
    _gate_stock,
    _gate_country,
    _gate_seller,
    _gate_fitment,
]


def gate_check(c: Candidate, cfg: GatesConfig) -> Optional[str]:
    """走一遍所有 gate. 返回第一个拒绝原因, 或 None 表示放行."""
    for g in _GATES:
        reason = g(c, cfg)
        if reason:
            return reason
    return None