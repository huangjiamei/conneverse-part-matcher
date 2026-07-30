"""
第 2 层 · 系统打分 (0-100) —— 按 OPTIMIZER.md §三 实现。

三个大分, 标准固定, 用户不能调:
  price_score    landed (标价+运费) 相对锚点的比值
  speed_score    delivery_days_max 在 [D_fast, D_slow] 上的线性插值
  quality_score  0.50·seller + 0.25·condition + 0.15·assurance + 0.10·popularity

统一约定: 缺信号 → 中性 50, 不惩罚也不奖励。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from .candidate import Candidate

NEUTRAL = 50.0


@dataclass
class ScoringConfig:
    # ---- price (§三.1) ----
    # 离群保护: 最低 landed < outlier_ratio × 次低 landed 时, 锚点取次低。
    anchor_outlier_ratio: float = 0.6

    # ---- speed (§三.2) ----
    d_fast: int = 2      # 这么快 = 100 分
    d_slow: int = 14     # 这么慢 = 0 分

    # ---- quality 大分权重 (§三.3) ----
    w_seller: float = 0.50
    w_condition: float = 0.25
    w_assurance: float = 0.15
    w_popularity: float = 0.10

    # ---- popularity 标定点 (§三.3 ④): sold=50 时满分 ----
    popularity_full_scale_sold: int = 50


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


# =====================================================================
# price
# =====================================================================

def landed_cost(c: Candidate) -> Optional[float]:
    """landed = price + shipping_cost。运费缺失 → None (不当免运, §一)。"""
    if c.price is None or c.price <= 0:
        return None
    if c.shipping_cost is None:
        return None
    return float(c.price) + float(c.shipping_cost)


def compute_landed_anchor(
    landeds: Iterable[Optional[float]],
    outlier_ratio: float = 0.6,
) -> Optional[float]:
    """
    锚点 = 最低 landed; 但最低 < outlier_ratio × 次低时改用次低 (离群保护)。

    防的是"一个 $3 的螺丝混进保险杠里", 让整组价格分被踩到接近 0。
    全组都没 landed → None (调用方给中性 50)。

    只有 >=3 条有价候选才启用离群保护: 两条的时候没有参照群, 判不出谁是离群点 ——
    真按次低取, 锚点就落到那一条更贵的上, 两条的价格分双双 clamp 成 100, 价格维度
    直接失去分辨率。
    """
    values: Sequence[float] = sorted(v for v in landeds if v is not None and v > 0)
    if not values:
        return None
    if len(values) >= 3 and values[0] < outlier_ratio * values[1]:
        return values[1]
    return values[0]


def price_score(landed: Optional[float], anchor: Optional[float]) -> float:
    """
    价格分 = clamp(100 × anchor / landed)。

    landed 缺失 (运费拿不到) → 中性 50, 且这条也不参与锚点计算。
    landed <= 0 → 0。单候选时 anchor == 自己 → 100。
    """
    if landed is None or anchor is None:
        return NEUTRAL
    if landed <= 0:
        return 0.0
    return _clamp(100.0 * anchor / landed)


# =====================================================================
# speed
# =====================================================================

def speed_score(dmax: Optional[int], d_fast: int = 2, d_slow: int = 14) -> float:
    """速度分。缺预估 (9%) → 中性 50; 急件靠第 1 层 gate 滤, 不在这里罚。"""
    if dmax is None:
        return NEUTRAL
    if d_slow == d_fast:
        return NEUTRAL
    return _clamp(100.0 * (d_slow - dmax) / (d_slow - d_fast))


# =====================================================================
# quality 的四个子分
# =====================================================================

def seller_subscore(c: Candidate) -> float:
    """
    seller = 60 + 好评率档位 + (top_rated → +20)

    好评率挤在 98.5-100, 分辨率低, 主区分信号是 top_rated。
    好评率缺失 (0 / None) → 中性 50, 不能当成 "<98 → -30"。
    """
    pct = c.seller_feedback_pct
    if not pct or pct <= 0:
        return NEUTRAL

    if pct >= 99.5:
        score = 60.0 + 20.0
    elif pct >= 99.0:
        score = 60.0 + 10.0
    elif pct >= 98.0:
        score = 60.0
    else:
        score = 60.0 - 30.0

    if c.top_rated:
        score += 20.0
    return _clamp(score)


def condition_subscore(c: Candidate) -> float:
    """按 conditionId 分桶 (§三.3 ②)。7000 由第 1 层排除, 到不了这里。"""
    cid = c.condition_id
    if cid is None:
        return NEUTRAL
    if cid == 1000:
        return 100.0
    if cid == 1500:
        return 90.0
    if 2000 <= cid <= 2999:
        return 82.0
    if 3000 <= cid <= 5999:
        return 75.0
    if cid == 6000:
        return 60.0
    return NEUTRAL


def warranty_subscore(years: Optional[float]) -> float:
    """
    Lifetime→100 / ≥3yr→80 / ≥1yr→60 / <1yr→40 / 明确无→30 / 缺失→50

    注意 0.0 和 None 是两回事: adapter 把 "None"/"No" 解析成 0.0 年 (卖家明确说没保修,
    比不吭声更差 → 30), 字段整个缺失才是 None (无信息 → 中性 50)。
    """
    if years is None:
        return NEUTRAL
    if years >= 99:            # Lifetime 哨兵值
        return 100.0
    if years >= 3:
        return 80.0
    if years >= 1:
        return 60.0
    if years > 0:              # <1 年: "Yes"(0.5) / "6 Months" / "60 Day" 都在这档
        return 40.0
    return 30.0                # 明确无 ("None" / "No" → 0.0 年)


def returns_subscore(accepted: Optional[bool], period_days: Optional[int]) -> float:
    """≥30天→100 / 接受(窗口未知)→60 / 否→0 / 缺失→50"""
    if accepted is None:
        return NEUTRAL
    if not accepted:
        return 0.0
    if period_days is None:
        return 60.0            # 接受但窗口未知
    if period_days >= 30:
        return 100.0
    return 60.0


def assurance_subscore(c: Candidate) -> float:
    """assurance = 0.6 × warranty + 0.4 × returns。两边各自缺失 → 各自 50。"""
    return _clamp(
        0.6 * warranty_subscore(c.warranty_years)
        + 0.4 * returns_subscore(c.returns_accepted, c.return_period_days)
    )


def popularity_subscore(c: Candidate, full_scale_sold: int = 50) -> float:
    """
    热度分。sold_qty 中位数就是 0, 所以 0 当"未知"给中性 50, 不当差评。
    >0: 50 + 50 × log10(1+sold)/log10(1+full_scale), 封顶 100。
    """
    sold = c.sold_qty
    if sold is None or sold <= 0:
        return NEUTRAL
    return _clamp(NEUTRAL + 50.0 * math.log10(1 + sold) / math.log10(1 + full_scale_sold))


def quality_score(c: Candidate, cfg: Optional[ScoringConfig] = None) -> float:
    """质量分 = 0.50·seller + 0.25·condition + 0.15·assurance + 0.10·popularity。"""
    cfg = cfg or ScoringConfig()
    return _clamp(
        cfg.w_seller * seller_subscore(c)
        + cfg.w_condition * condition_subscore(c)
        + cfg.w_assurance * assurance_subscore(c)
        + cfg.w_popularity * popularity_subscore(c, cfg.popularity_full_scale_sold)
    )


def quality_breakdown(c: Candidate, cfg: Optional[ScoringConfig] = None) -> dict:
    """四个子分的明细, 调试/展示用 (不进排序)。"""
    cfg = cfg or ScoringConfig()
    return {
        "seller": seller_subscore(c),
        "condition": condition_subscore(c),
        "assurance": assurance_subscore(c),
        "popularity": popularity_subscore(c, cfg.popularity_full_scale_sold),
        # assurance 的两个分量, 便于定位是保修还是退货把分拉低的
        "warranty": warranty_subscore(c.warranty_years),
        "returns": returns_subscore(c.returns_accepted, c.return_period_days),
    }
