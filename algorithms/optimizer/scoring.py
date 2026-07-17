"""
软打分 (Soft Scores).

两个维度:
  - quality_score (0-100): 卖家信誉 + 保修 + 退货 + top rated 等信号加总
  - price_score (0-100): 相对最便宜合格候选的比例

设计原则:
  - 每个信号缺失 (None) 就 skip, 不惩罚缺信号的候选
  - 加分项和乘数分开算, 最后 clamp 到 [0, 100]
  - 内部分数, UI 不直接展示 (跟 JS 版一样, 只展示 tier/badge)
"""
from __future__ import annotations

from dataclasses import dataclass

from .candidate import Candidate


@dataclass
class ScoringConfig:
    # Seller reputation bayesian shrinkage
    # 卖家 feedback_pct 用贝叶斯收缩到先验, 评价数越少越拉向先验
    # 先验从数据看中位数 99.8, 但用 98 保守一点
    seller_pct_prior: float = 98.0
    seller_pct_pseudo: int = 1000       # 相当于假设有 1000 条平均评价

    # Quality bump/haircut
    warranty_year_1_bonus: float = 5.0
    warranty_year_3_bonus: float = 10.0    # 追加 (总共 15)
    warranty_lifetime_bonus: float = 15.0  # 追加 (总共 30)
    top_rated_bonus: float = 5.0
    returns_bonus: float = 5.0             # returns_accepted + return_period >= 30
    sold_qty_signal_threshold: int = 100   # 销量 >100 认为 fitment 有社会证明
    sold_qty_bonus: float = 5.0

    # Self-host haircut (eBay 用不上, 留接口)
    self_hosted_haircut: float = 0.3

    # 权重归一化在 optimize() 里做
    # 这里的默认 preset 混合在 presets.py


def _bayes_seller_pct(pct: float, count: int, prior: float, pseudo: int) -> float:
    """把 seller_feedback_pct 用贝叶斯收缩. 评价少的卖家往先验拉."""
    if count <= 0:
        return prior
    return (pct * count + prior * pseudo) / (count + pseudo)


def quality_score(c: Candidate, cfg: ScoringConfig) -> float:
    """
    质量分 0-100.

    骨架:
      1. 卖家信誉 (贝叶斯收缩过的 feedback_pct) 映射到 0-100 基础分
      2. 加 warranty / top_rated / returns / sold_qty bonus
      3. 如果拿到了 product_rating (未来), 融合进去
      4. 如果拿到了 review_recency (未来), 作为最后乘子
    """
    # ---- 1. 基础: 卖家信誉 ----
    adj_pct = _bayes_seller_pct(
        c.seller_feedback_pct or 0.0,
        c.seller_feedback_count or 0,
        cfg.seller_pct_prior,
        cfg.seller_pct_pseudo,
    )
    # 映射: 95% -> 40, 99.5% -> 80, 100% -> 90
    # 用 (adj - 92) / 8 * 60 + 30, 然后 clamp
    q = (adj_pct - 92.0) / 8.0 * 60.0 + 30.0
    q = max(0.0, min(90.0, q))  # 卖家信誉最多顶到 90, 留 10 分给其他 bonus

    # ---- 2. Warranty bonus (阶梯) ----
    if c.warranty_years is not None:
        if c.warranty_years >= 99:  # Lifetime
            q += cfg.warranty_lifetime_bonus
        elif c.warranty_years >= 3:
            q += cfg.warranty_year_3_bonus
        elif c.warranty_years >= 1:
            q += cfg.warranty_year_1_bonus

    # ---- 3. Top Rated Seller bonus ----
    if c.top_rated:
        q += cfg.top_rated_bonus

    # ---- 4. Returns bonus ----
    if c.returns_accepted and (c.return_period_days or 0) >= 30:
        q += cfg.returns_bonus

    # ---- 5. Social proof: 销量 ----
    if c.sold_qty >= cfg.sold_qty_signal_threshold:
        q += cfg.sold_qty_bonus

    # ---- 6. 未来接入: product_rating ----
    # 如果 product_rating 拿到了, 融合. 目前恒为 None, 跳过.
    if c.product_rating is not None and c.product_review_count and c.product_review_count > 0:
        # 简单混合: 产品星级也做贝叶斯, 转成 0-100 后跟 q 平均
        pr = c.product_rating
        # 4.2 星先验, pseudo 20 (跟 JS 版一致)
        pr_adj = (pr * c.product_review_count + 4.2 * 20) / (c.product_review_count + 20)
        pr_score = max(0.0, min(100.0, (pr_adj - 3.5) / 1.4 * 60 + 40))
        q = (q + pr_score) / 2.0

    # ---- 7. Self-host haircut (JS 版遗留, eBay 用不上) ----
    if c.is_self_hosted_rating:
        q -= cfg.self_hosted_haircut * 10  # 每 0.3 星对应 3 分

    # ---- 8. Review recency 乘子 (JS 版), 拿到才生效 ----
    if c.review_recency is not None:
        # 0-100 recency, 映射到 [0.65, 1.0] 乘子
        q *= 0.65 + 0.35 * (c.review_recency / 100.0)

    return max(0.0, min(100.0, q))


def price_score(price: float, min_eligible_price: float) -> float:
    """
    价格分 0-100. 最便宜合格候选 = 100, 其他按比例.

    参考点是合格候选中最便宜的 (不是全体候选), gate 掉的价格不影响锚点.
    """
    if min_eligible_price <= 0 or price <= 0:
        return 0.0
    return max(0.0, min(100.0, 100.0 * min_eligible_price / price))