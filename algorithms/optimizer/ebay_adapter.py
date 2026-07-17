"""
eBay Browse API 适配层.

把 raw_response (getItem 返回的 dict) 转成 Candidate 实例.
所有解析逻辑集中在这里, 未来接别的数据源就加一个新 adapter.
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


def _find_aspect(raw: dict, name: str) -> Optional[str]:
    """从 localizedAspects 里找指定 name 的 value."""
    for a in (raw.get("localizedAspects") or []):
        if a.get("name") == name:
            return a.get("value")
    return None


def _parse_warranty_years(v: Optional[str]) -> Optional[float]:
    """
    Warranty aspect 值多样: "1 Year", "2 Years", "Lifetime", "60 Day", "Yes", "None"...
    归一化到年数. 无法解析返回 None.

    覆盖:
      "1 Year", "1Year", "1 year unlimited-mileage warranty" -> 1.0
      "Lifetime" -> 99.0 (哨兵值)
      "60 Day" -> 0.16
      "6 Months" -> 0.5
      "Yes" -> 0.5 (有但没说, 保守)
      "None" / "No" -> 0.0
    """
    if not v:
        return None
    v = v.strip().lower()
    if v in ("yes",):
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


def build_candidate_from_ebay(raw: dict, now: Optional[datetime] = None) -> Candidate:
    """
    从 eBay Browse API /item/{id} 返回的 dict 构造 Candidate.

    now: 用于计算 delivery_days_min/max. 默认 UTC now, 测试时可以固定.
    """
    if raw is None:
        raise ValueError("raw is None")

    # ---- 识别 ----
    item_id = raw.get("legacyItemId") or raw.get("itemId", "")
    title = raw.get("title") or ""
    brand = raw.get("brand") or ""

    # ---- 价格 ----
    price = _to_float(raw.get("price", {}).get("value"))

    # ---- 运费 ----
    shipping_cost = 0.0
    shipping_opts = raw.get("shippingOptions") or []
    if shipping_opts:
        shipping_cost = _to_float(shipping_opts[0].get("shippingCost", {}).get("value"))

    # ---- Condition ----
    condition = raw.get("condition") or ""

    # ---- 库存 ----
    avail_status = "IN_STOCK"
    available_qty: Optional[int] = None
    sold_qty = 0
    avails = raw.get("estimatedAvailabilities") or []
    if avails:
        av = avails[0] or {}
        avail_status = av.get("estimatedAvailabilityStatus") or "IN_STOCK"
        aq = av.get("estimatedAvailableQuantity")
        if isinstance(aq, int):
            available_qty = aq
        sq = av.get("estimatedSoldQuantity")
        if isinstance(sq, int):
            sold_qty = sq

    # ---- Seller ----
    seller = raw.get("seller") or {}
    seller_pct = _to_float(seller.get("feedbackPercentage"))
    seller_count = seller.get("feedbackScore") or 0
    if not isinstance(seller_count, int):
        try:
            seller_count = int(seller_count)
        except (TypeError, ValueError):
            seller_count = 0

    # ---- Top rated ----
    top_rated = bool(raw.get("topRatedBuyingExperience"))

    # ---- Delivery ----
    delivery_min = delivery_max = None
    if shipping_opts:
        so = shipping_opts[0]
        delivery_min = _days_from_now(_parse_iso_dt(so.get("minEstimatedDeliveryDate")), now)
        delivery_max = _days_from_now(_parse_iso_dt(so.get("maxEstimatedDeliveryDate")), now)

    # ---- Returns ----
    returns = raw.get("returnTerms") or {}
    returns_accepted = bool(returns.get("returnsAccepted"))
    return_period_days = None
    rp = returns.get("returnPeriod") or {}
    if rp.get("value") is not None and rp.get("unit") == "CALENDAR_DAY":
        try:
            return_period_days = int(rp["value"])
        except (TypeError, ValueError):
            pass

    # ---- Warranty (从 aspects 提取, 归一化到年数) ----
    warranty_raw = _find_aspect(raw, "Manufacturer Warranty") or _find_aspect(raw, "Warranty")
    warranty_years = _parse_warranty_years(warranty_raw)

    # ---- Location ----
    country = ((raw.get("itemLocation") or {}).get("country")) or ""

    return Candidate(
        item_id=str(item_id),
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
        # 以下 eBay 拿不到, 保持 None
        product_rating=None,
        product_review_count=None,
        fitment_complaint_rate=None,
        fitment_review_sample=None,
        review_recency=None,
        is_self_hosted_rating=False,
        raw=raw,
    )