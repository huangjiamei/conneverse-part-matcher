"""
Matcher 输出 -> Candidate 的适配层.

matcher pipeline 输出的 candidate_info_list 每一项是精简过的 dict, 不是 eBay
getItem 原始返回. optimizer 消费的字段集中在 candidate['optimizer_fields'] 里.

如果 candidate 是老版 pipeline 输出 (没有 optimizer_fields), 回退到"能拿多少算多少",
拿不到的字段全 None. 这样切换 pipeline 时不会全线报错.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional

from .candidate import Candidate


def _to_float(v: Any) -> float:
    if v is None or v == "":
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _to_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, int):
        return v
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _parse_warranty_years(v: Optional[str]) -> Optional[float]:
    """
    Warranty aspect 值多样: "1 Year", "2 Years", "Lifetime", "60 Day", "Yes", "None"...
    归一化到年数. 无法解析返回 None.

    覆盖:
      "1 Year", "1Year", "1-year unlimited-mileage warranty" -> 1.0
      "Lifetime" -> 99.0 (哨兵值)
      "60 Day" -> 0.16
      "6 Months" -> 0.5
      "Yes" -> 0.5 (有但没说, 保守)
      "None" / "No" -> 0.0
    """
    if not v:
        return None
    v = v.strip().lower()
    if v == "yes":
        return 0.5
    if v in ("no", "none"):
        return 0.0
    if "lifetime" in v:
        return 99.0
    m = re.match(r"(\d+)\s*[- ]?\s*year", v)
    if m:
        return float(m.group(1))
    m = re.match(r"(\d+)\s*[- ]?\s*month", v)
    if m:
        return float(m.group(1)) / 12
    m = re.match(r"(\d+)\s*[- ]?\s*day", v)
    if m:
        return float(m.group(1)) / 365
    return None


def _parse_iso_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _days_from_now(dt: Optional[datetime], now: Optional[datetime] = None) -> Optional[int]:
    if dt is None:
        return None
    now = now or datetime.now(timezone.utc)
    return max(0, (dt - now).days)


def build_candidate_from_matcher(
    candidate_info: dict,
    now: Optional[datetime] = None,
) -> Candidate:
    """
    从 matcher pipeline 的 candidate_info_list[i] 构造 Candidate.

    candidate_info 至少包含: title, item_id, price, condition, candidate_label
    可选包含 optimizer_fields (新版 pipeline 输出)

    now: 用于计算 delivery_days_min/max, 测试可固定.
    """
    if candidate_info is None:
        raise ValueError("candidate_info is None")

    # ---- 从顶层拿现有字段 (老版和新版都有的) ----
    item_id = str(candidate_info.get("item_id") or "")
    title = candidate_info.get("title") or ""
    condition = candidate_info.get("condition") or ""
    price = _to_float((candidate_info.get("price") or {}).get("value"))

    # brand 从 compatibility 里拿 (matcher 已经抽出来了)
    compat = candidate_info.get("compatibility") or {}
    brand = compat.get("Brand") or compat.get("Make") or ""

    # ---- 从 optimizer_fields 拿新增字段, 老版 candidate 没这个 key 就退化 ----
    opt = candidate_info.get("optimizer_fields") or {}

    seller_pct = _to_float(opt.get("seller_feedback_pct"))
    seller_count = _to_int(opt.get("seller_feedback_count")) or 0
    top_rated = bool(opt.get("top_rated"))

    avail_status = opt.get("availability_status") or "IN_STOCK"
    available_qty = _to_int(opt.get("available_qty"))
    sold_qty = _to_int(opt.get("sold_qty")) or 0

    shipping_cost = _to_float(opt.get("shipping_cost"))
    delivery_min = _days_from_now(_parse_iso_dt(opt.get("delivery_min_date")), now)
    delivery_max = _days_from_now(_parse_iso_dt(opt.get("delivery_max_date")), now)

    returns_accepted = bool(opt.get("returns_accepted"))
    return_period_days = _to_int(opt.get("return_period_days"))

    warranty_years = _parse_warranty_years(opt.get("warranty_raw"))

    country = opt.get("country") or ""

    return Candidate(
        item_id=item_id,
        title=title,
        brand=brand,
        price=price,
        shipping_cost=shipping_cost,
        condition=condition,
        availability_status=avail_status,
        available_qty=available_qty,
        sold_qty=sold_qty,
        seller_feedback_pct=seller_pct,
        seller_feedback_count=seller_count,
        top_rated=top_rated,
        delivery_days_min=delivery_min,
        delivery_days_max=delivery_max,
        returns_accepted=returns_accepted,
        return_period_days=return_period_days,
        warranty_years=warranty_years,
        country=country,
        # 以下 eBay/matcher 都拿不到, 保持 None
        product_rating=None,
        product_review_count=None,
        fitment_complaint_rate=None,
        fitment_review_sample=None,
        review_recency=None,
        is_self_hosted_rating=False,
        raw=candidate_info,
    )