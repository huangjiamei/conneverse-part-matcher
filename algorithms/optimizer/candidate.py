"""
Candidate dataclass — 优化器的输入。

字段设计遵循一个原则:
  - eBay 现在拿得到的字段填真值
  - JS 版有但 eBay 拿不到的字段 (rating/reviews_count/fitment_complaint 等)
    保留字段名和类型, 值给 None
  - Gate 和 scoring 逻辑对 None 友好, 缺什么信号就跳过什么信号,
    不做插值也不用 magic default
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Candidate:
    # ---------- 识别 ----------
    item_id: str
    title: str
    brand: str = ""

    # ---------- 核心经济字段 (eBay 100% 填充) ----------
    price: float = 0.0                  # 单价, USD
    shipping_cost: float = 0.0          # 运费, 0 = 免运费
    condition: str = "New"              # "New" / "Used" / "New other (see details)"

    # ---------- 库存 ----------
    availability_status: str = "IN_STOCK"  # IN_STOCK / OUT_OF_STOCK
    available_qty: Optional[int] = None   # 卖家自填, 71% 填充率, 软信号
    sold_qty: int = 0                    # 累计销量, 热度信号

    # ---------- 卖家信誉 (eBay 有的) ----------
    seller_feedback_pct: float = 0.0    # 0-100, 中位数 99.8
    seller_feedback_count: int = 0      # 累计好评数
    top_rated: bool = False              # eBay Top Rated Seller 标

    # ---------- 交付/保障 ----------
    delivery_days_min: Optional[int] = None  # 到"今天"的最短天数
    delivery_days_max: Optional[int] = None
    returns_accepted: bool = False
    return_period_days: Optional[int] = None
    warranty_years: Optional[float] = None    # 从 aspect 归一化, 详见 adapter

    # ---------- 地理 ----------
    country: str = ""  # "US", "CN", ...

    # =====================================================================
    # 以下字段是 JS 版有、eBay 拿不到、但保留接口的.
    # 当前一律 None. 拿到数据后填, gate/scoring 会自动生效.
    # =====================================================================

    # 产品级星级 (0-5). eBay 只给卖家级信誉, 不给产品星级.
    # 未来可能从 Amazon/Google Shopping 富化.
    product_rating: Optional[float] = None

    # 产品评价数. 同上, eBay 没有产品评价.
    product_review_count: Optional[int] = None

    # Fitment 抱怨率 (0-1) 和样本量.
    # 需要爬 review 内容 + NLP, Browse API 不给 review.
    fitment_complaint_rate: Optional[float] = None
    fitment_review_sample: Optional[int] = None

    # Review 时效性 (0-100). 拿不到评论就没法算.
    review_recency: Optional[float] = None

    # 是否卖家自站评论 (JS 版 house). eBay 全平台评论, 恒 False.
    is_self_hosted_rating: bool = False

    # =====================================================================
    # 兜底: 保留原始数据引用, 方便 debug 或后续加字段
    # =====================================================================
    raw: Optional[dict] = field(default=None, repr=False, compare=False)