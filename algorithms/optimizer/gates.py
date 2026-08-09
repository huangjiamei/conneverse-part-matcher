"""
第 1 层 · 硬门槛 (Hard Gates) —— 按 OPTIMIZER.md §四 实现。

每个 gate 独立判断, 返回 None 表示放行, 返回字符串表示拒绝原因.
最终 gate_check() 走一遍所有 gates, 返回第一个拒绝原因或 None.

设计原则:
  - 缺信号时放行 (None-tolerant), 不因为数据不全就 knock 掉候选
  - 拒绝原因是机器可读字符串, 上层可以统计 funnel
  - Gate 配置 (阈值) 通过 GatesConfig 传, 不 hardcode

三类 gate:
  固定不可关     For parts 排除 (condition_id==7000)、卖家最低线 (98% / 100)、
                 warranty ≥1 年 (TOLERANT, 缺失放行)
  preset 拨     在库门槛
  独立开关       仅美国 (require_domestic)、到货硬截止 (max_delivery_days), 均默认关

新件/二手不再进 gate: "偏新" 完全是软偏好, 由质量分的件况子分体现
(New=100 > New other=90 > Reman=82 > Used=75 > Acceptable=60)。
For parts (7000) 是功能性排除 —— 那是坏件, 不是 "旧一点的件"。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .candidate import Candidate

CONDITION_FOR_PARTS = 7000        # 坏件/拆车件, 永远排除


@dataclass
class GatesConfig:
    # ---------- 固定门槛 (§四: 所有 preset 共享, 不由 preset 拨) ----------
    # 卖家最低线: 只挡近乎零记录的新号, 不偏好大卖家。
    # v4 数据 (7,809 item): 评价数 <100 切底部 4.6%, 好评率 <98 切约 5%。
    min_seller_feedback_pct: float = 98.0
    min_seller_feedback_count: int = 100

    # warranty 最低线: ≥1 年。TOLERANT 口径 —— 只踢"能证明不达标"的
    # (明确写无保修 / 明确 <12 个月), 缺失和含糊一律放行。
    # v4 数据 (7,552 item, verified 3,809): 该字段填充率只有 62%, 严格口径会砍掉
    # 一半候选且其中 82% 是"没填"而非"保修短", 故取 TOLERANT。
    require_warranty: bool = True
    min_warranty_months: float = 12.0

    # ---------- preset 拨的开关 ----------
    require_in_stock: bool = True         # 在库门槛

    # ---------- 独立开关 (不绑 preset, 默认关) ----------
    # "仅美国": 发货地约束, 不是质量信号, 谁有合规要求谁单独开。
    require_domestic: bool = False
    # 到货硬截止: 高级入口显式设死线时才开。Rush 不带它 —— 校准集上 3 天截止
    # 只放行 4.1% 的候选, 当默认值太狠, 急件靠 speed 权重排序即可。
    max_delivery_days: Optional[int] = None

    # ---------- 休眠 (无数据, 有数据自动生效) ----------
    max_fitment_complaint_rate: float = 0.15  # 只在有 sample 时生效
    min_fitment_sample: int = 8               # sample < 此值不启用 fitment gate


def _gate_for_parts(c: Candidate, cfg: GatesConfig) -> Optional[str]:
    """For parts / not working 永远排除。固定, 不可关。"""
    if c.condition_id == CONDITION_FOR_PARTS:
        return "condition:for_parts"
    return None


def _gate_stock(c: Candidate, cfg: GatesConfig) -> Optional[str]:
    if not cfg.require_in_stock:
        return None
    if c.availability_status and c.availability_status != "IN_STOCK":
        return f"stock:{c.availability_status.lower()}"
    return None


def _gate_seller(c: Candidate, cfg: GatesConfig) -> Optional[str]:
    """卖家最低线。pct=0 / count=0 视为缺信号放行 (eBay 填充率 ~100%, 极少触发)。"""
    if c.seller_feedback_pct and c.seller_feedback_pct < cfg.min_seller_feedback_pct:
        return f"seller_feedback:{c.seller_feedback_pct:.1f}%"
    if c.seller_feedback_count and c.seller_feedback_count < cfg.min_seller_feedback_count:
        return f"seller_count:{c.seller_feedback_count}"
    return None


def _gate_warranty(c: Candidate, cfg: GatesConfig) -> Optional[str]:
    """
    warranty ≥1 年 (TOLERANT)。只踢能证明不达标的:
      明确写"无保修"            -> 砍
      解析出时长且 <12 个月       -> 砍
      缺失 / 含糊 ("Yes"/"Other") -> 放行 (不填 ≠ 没保修)
      ≥12 个月 / Lifetime        -> 放行
    """
    if not cfg.require_warranty:
        return None
    if c.warranty_none:
        return "warranty:none"
    if c.warranty_months is not None and c.warranty_months < cfg.min_warranty_months:
        return f"warranty:{int(c.warranty_months)}mo"
    return None


def _gate_delivery(c: Candidate, cfg: GatesConfig) -> Optional[str]:
    """到货硬截止 (仅 Rush)。没有预估天数 (9%) 时放行, 不猜。"""
    if cfg.max_delivery_days is None or c.delivery_days_max is None:
        return None
    if c.delivery_days_max > cfg.max_delivery_days:
        return f"delivery:{c.delivery_days_max}d>{cfg.max_delivery_days}d"
    return None


def _gate_country(c: Candidate, cfg: GatesConfig) -> Optional[str]:
    if not cfg.require_domestic:
        return None
    if c.country and c.country != "US":
        return f"country:{c.country}"
    return None


def _gate_fitment(c: Candidate, cfg: GatesConfig) -> Optional[str]:
    # 休眠中: 只在有实际样本且样本 >= 阈值时才启用
    if c.fitment_complaint_rate is None or c.fitment_review_sample is None:
        return None  # 没数据就不 gate
    if c.fitment_review_sample < cfg.min_fitment_sample:
        return None  # 样本太小不判
    if c.fitment_complaint_rate > cfg.max_fitment_complaint_rate:
        return f"fitment_risk:{c.fitment_complaint_rate:.0%}"
    return None


# 顺序: 固定门槛在前 (最常命中), 可选开关在后
_GATES = [
    _gate_for_parts,
    _gate_stock,
    _gate_seller,
    _gate_warranty,
    _gate_delivery,
    _gate_country,
    _gate_fitment,
]


def gate_check(c: Candidate, cfg: GatesConfig) -> Optional[str]:
    """走一遍所有 gate. 返回第一个拒绝原因, 或 None 表示放行."""
    for g in _GATES:
        reason = g(c, cfg)
        if reason:
            return reason
    return None
