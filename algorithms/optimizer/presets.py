"""
场景预设: gates + weights + scoring 打包组合.

设计跟 JS 版一致——advisor 不看 sliders, 只选场景.
每个 preset 是 DemandContext 的一个具体实例.
"""
from __future__ import annotations

from dataclasses import dataclass

from .gates import GatesConfig
from .scoring import ScoringConfig


@dataclass
class Weights:
    price: float = 50.0
    quality: float = 50.0


@dataclass
class Preset:
    name: str
    description: str
    gates: GatesConfig
    scoring: ScoringConfig
    weights: Weights


# 场景 1: 当天要修完, 急件
SAME_DAY_JOB = Preset(
    name="sameDayJob",
    description="车已经在架子上, 今天必须搞定",
    gates=GatesConfig(
        require_in_stock=True,
        min_seller_feedback_pct=97.0,   # 更严, 不想 fitment 出问题
        min_seller_feedback_count=100,
        require_domestic=True,           # US only, 保证配送准时
    ),
    scoring=ScoringConfig(),
    weights=Weights(price=40, quality=60),
)


# 场景 2: 省钱优先, 交付有时间
COST_FIRST = Preset(
    name="costFirst",
    description="用户不急, 越便宜越好, 只要不是明显低质卖家",
    gates=GatesConfig(
        require_in_stock=True,
        min_seller_feedback_pct=95.0,
        min_seller_feedback_count=50,
        require_domestic=False,          # CN 卖家也可, 便宜
    ),
    scoring=ScoringConfig(),
    weights=Weights(price=80, quality=20),
)


# 场景 3: 质量优先, 不介意贵一点
QUALITY_FIRST = Preset(
    name="qualityFirst",
    description="客户对可靠性敏感 (豪华车, 高价车主, 大保险公司)",
    gates=GatesConfig(
        require_in_stock=True,
        min_seller_feedback_pct=98.0,    # 卡最高
        min_seller_feedback_count=500,   # 老牌大卖家
        require_domestic=True,
    ),
    scoring=ScoringConfig(),
    weights=Weights(price=15, quality=85),
)


# 场景 4: 计划采购, 可以等
SCHEDULED = Preset(
    name="scheduled",
    description="非急件, 允许 backorder, 价格质量均衡",
    gates=GatesConfig(
        require_in_stock=False,          # 允许 backorder
        min_seller_feedback_pct=95.0,
        min_seller_feedback_count=50,
        require_domestic=False,
    ),
    scoring=ScoringConfig(),
    weights=Weights(price=55, quality=45),
)


PRESETS = {
    "sameDayJob": SAME_DAY_JOB,
    "costFirst": COST_FIRST,
    "qualityFirst": QUALITY_FIRST,
    "scheduled": SCHEDULED,
}


def get_preset(name: str) -> Preset:
    if name not in PRESETS:
        raise ValueError(f"Unknown preset: {name}. Available: {list(PRESETS)}")
    return PRESETS[name]