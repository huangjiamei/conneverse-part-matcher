"""
第 3 层 · preset —— 按 OPTIMIZER.md §五 实现。

一个 preset = 第 1 层开关 + 第 3 层权重 的打包。打分标准 (第 2 层) 固定, preset 不动它。

所有 preset 共享的固定门槛 (不在这里写, 在 GatesConfig 的默认值里):
  For parts 排除、卖家最低线 98% / 100 条

"偏新" 是软偏好, 不是硬门槛: 只有 Premium 硬排除非新件, 其余三档都放行二手,
靠件况分 (New=100 > New other=90 > Reman=82 > Used=75) 自动降权。硬排除会让
二手主导的品类 (老车件、翼子板这类钣金件) 在默认档直接返回空。

两个独立开关, 任何 preset 都可叠加, 故都不写进 preset:
  require_domestic   "仅美国" 合规约束
  max_delivery_days  到货硬截止, 用户在高级入口显式设死线时才开
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .gates import GatesConfig
from .scoring import ScoringConfig


@dataclass
class Weights:
    """三维权重 (§五 表里的 price/speed/quality)。数值是相对比例, optimize() 里归一化。"""
    price: float = 35.0
    speed: float = 30.0
    quality: float = 35.0


@dataclass
class Preset:
    name: str
    description: str
    gates: GatesConfig
    scoring: ScoringConfig
    weights: Weights


# 急件: 今明两天必须到 —— 靠 speed=60 的权重把快的排前, 不硬过滤。
# 硬截止 (max_delivery_days) 是独立开关, 用户在高级入口显式设死线时才开:
# 校准集上 3 天截止只放行 4.1% 的候选, 当 preset 默认值太狠。
RUSH = Preset(
    name="Rush",
    description="今明两天必须到, 车还在架子上",
    gates=GatesConfig(require_in_stock=True, allow_used=True),
    scoring=ScoringConfig(),
    weights=Weights(price=15, speed=60, quality=25),
)

# 均衡 (默认): 常规采购
BALANCED = Preset(
    name="Balanced",
    description="常规采购, 价格/速度/质量均衡",
    gates=GatesConfig(require_in_stock=True, allow_used=True),
    scoring=ScoringConfig(),
    weights=Weights(price=35, speed=30, quality=35),
)

# 省钱: 不急, 越便宜越好 —— 放开 backorder 和二手
BUDGET = Preset(
    name="Budget",
    description="不急, 越便宜越好; 允许 backorder 和二手件",
    gates=GatesConfig(require_in_stock=False, allow_used=True),
    scoring=ScoringConfig(),
    weights=Weights(price=60, speed=10, quality=30),
)

# 优质: 高端车 / 严苛保险 —— 只收新件
# 卖家门槛不单独抬 (§四: 靠质量分权重区分, 不靠 gate 偏好大卖家)
PREMIUM = Preset(
    name="Premium",
    description="高端车 / 严苛保险, 只收新件",
    gates=GatesConfig(require_in_stock=True, require_new=True),
    scoring=ScoringConfig(),
    weights=Weights(price=15, speed=25, quality=60),
)


# 老 preset 名 -> 新 preset。service.py / Next.js 里还在传老名, 保持能跑。
# 映射按"场景最接近"取:
#   sameDayJob (当天要修完)   -> Rush
#   costFirst  (越便宜越好)    -> Budget
#   qualityFirst (质量优先)    -> Premium
#   scheduled  (计划采购/均衡) -> Balanced
LEGACY_PRESET_ALIASES = {
    "sameDayJob": "Rush",
    "costFirst": "Budget",
    "qualityFirst": "Premium",
    "scheduled": "Balanced",
}

_CANONICAL_PRESETS = {
    "Rush": RUSH,
    "Balanced": BALANCED,
    "Budget": BUDGET,
    "Premium": PREMIUM,
}

# PRESETS 同时收新名和老别名, 这样 `name in PRESETS` 的老校验代码不用改。
PRESETS = {
    **_CANONICAL_PRESETS,
    **{alias: _CANONICAL_PRESETS[target] for alias, target in LEGACY_PRESET_ALIASES.items()},
}

DEFAULT_PRESET = "Balanced"


def get_preset(name: str) -> Preset:
    if name not in PRESETS:
        raise ValueError(f"Unknown preset: {name}. Available: {list(PRESETS)}")
    return PRESETS[name]
