"""
Warranty aspect 解析 (ebay_adapter / matcher_adapter 共用)。

eBay 的 warranty 是卖家自填的可选 aspect, 值极杂 —— 7,552 item 的校准集里有 120 种
不同写法。解析出三个东西, 各有各的用途:

  years  : 兼容字段, 喂给质量分的 assurance 子分 (沿用老约定: Yes→0.5, No/None→0.0,
           Lifetime→99)。
  months : 只有"真解析出时长"时才有值, 给第 1 层 warranty gate 判 ≥1 年用。
           "Yes" 这种含糊值 years=0.5 但 months=None —— 它不是"半年保修",
           是"说了有但没说多久", 不能当成不达标砍掉。
  is_none: 卖家明确写了没保修 (None / No / No Warranty)。跟"字段整个缺失"要分开:
           前者是负面信息, 后者只是没填。

退货政策污染: 校准集里有 38 条把退货政策写进了 warranty 字段 ("30 Days Return
Accepted" / "See Return Policy"), 会被误解析成 30 天。含 return 字样的一律当没填。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# Lifetime 的哨兵值: years 沿用老的 99, months 用 1200 (100 年)
LIFETIME_YEARS = 99.0
LIFETIME_MONTHS = 1200.0

_RETURN_POLICY = re.compile(r"return", re.I)
_EXPLICIT_NONE = re.compile(r"^\s*(no|none|no\s*warranty|none\s*available)\s*$", re.I)
_YEAR = re.compile(r"(\d+(?:\.\d+)?)\s*[-\s]?\s*year", re.I)
_MONTH = re.compile(r"(\d+(?:\.\d+)?)\s*[-\s]?\s*month", re.I)
_DAY = re.compile(r"(\d+(?:\.\d+)?)\s*[-\s]?\s*day", re.I)


@dataclass(frozen=True)
class WarrantyInfo:
    years: Optional[float] = None      # assurance 子分用 (老约定)
    months: Optional[float] = None     # warranty gate 用 (只在真解析出时长时有值)
    is_none: bool = False              # 卖家明确声明无保修


def parse_warranty(value: Optional[str]) -> WarrantyInfo:
    """
    覆盖的写法 (校准集实测):
      "1 Year" / "1year" / "5Years" / "1-year unlimited-mileage warranty" -> 12 个月
      "24-months or unlimited mile AC Delco limited warranty"             -> 24 个月
      "90 Day" / "30 Days"                                                -> 3 / 1 个月
      "Lifetime" / "Limited Lifetime"                                     -> 哨兵
      "Yes"                                    -> years=0.5, months=None (含糊, gate 放行)
      "None" / "No" / "No Warranty"            -> years=0.0, is_none=True (gate 砍)
      "30 Days Return Accepted" / "See Return Policy" -> 全 None (退货政策, 不是保修)
      "Other" / "Unspecified Length" / "68165900AD"   -> 全 None (解析不出)
    """
    if not value or not str(value).strip():
        return WarrantyInfo()

    raw = str(value).strip()

    # 退货政策污染: 当没填处理, 不参与打分也不参与 gate
    if _RETURN_POLICY.search(raw):
        return WarrantyInfo()

    lowered = raw.lower()

    if _EXPLICIT_NONE.match(raw):
        # 明确无保修: years=0 让 assurance 给"明确无"那档 (30), months 留空由 is_none 表达
        return WarrantyInfo(years=0.0, months=None, is_none=True)

    if lowered == "yes":
        # 有保修但没说多久 —— 打分上给个保守值, gate 上按未知放行
        return WarrantyInfo(years=0.5, months=None)

    if "lifetime" in lowered:
        return WarrantyInfo(years=LIFETIME_YEARS, months=LIFETIME_MONTHS)

    m = _YEAR.search(raw)
    if m:
        years = float(m.group(1))
        return WarrantyInfo(years=years, months=years * 12.0)

    m = _MONTH.search(raw)
    if m:
        months = float(m.group(1))
        return WarrantyInfo(years=months / 12.0, months=months)

    m = _DAY.search(raw)
    if m:
        days = float(m.group(1))
        return WarrantyInfo(years=days / 365.0, months=days / 30.0)

    return WarrantyInfo()   # "Other" / "Unspecified" / 零件号 / 广告词
