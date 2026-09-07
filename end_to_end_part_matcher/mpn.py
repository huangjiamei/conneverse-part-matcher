from __future__ import annotations

import re
from typing import Any, Mapping

from .utils import normalize_mpn

YEAR_PATTERN = re.compile(r"^(19|20)\d{2}$")
ENGINE_DISPLACEMENT_PATTERN = re.compile(r"^\d+(\.\d+)?L$", re.I)
CYLINDER_PATTERN = re.compile(r"^\d+cyl(inder)?s?$", re.I)
TITLE_BLOCK_WORDS = {"FITS", "FOR", "REPLACES", "COMPATIBLE", "INTERCHANGE", "EQUIVALENT", "REPLACEMENT"}


def strip_capa_suffix(normalized: str) -> str:
    return re.sub(r"(PP|C)$", "", normalized) if normalized and len(normalized) >= 7 else normalized


def parse_dirty_part_numbers(raw_value: Any) -> list[str]:
    return [x.strip() for x in re.split(r"[,;/|\s]+", str(raw_value or "")) if x.strip() and len(x.strip()) > 2]


def looks_like_part_number(text: Any) -> bool:
    s = str(text or "").strip()
    return not (
        len(s) > 20 or len(s) < 3 or len(s.split()) > 3 or not re.search(r"\d", s)
        or YEAR_PATTERN.match(s) or ENGINE_DISPLACEMENT_PATTERN.match(s) or CYLINDER_PATTERN.match(s)
        or len(re.findall(r"\d", s)) < 2
    )


def _aspect_value(item: Mapping[str, Any], name: str) -> Any:
    for aspect in item.get("localizedAspects") or []:
        if aspect.get("name") == name:
            return aspect.get("value")
    return None


def extract_interchange_part_numbers_from_description(item: Mapping[str, Any]) -> list[str]:
    desc = str(item.get("description") or "")
    match = re.search(
        r"Interchange Part Numbers[\s\S]*?Part Numbers[\s\S]*?<div class=\"nine columns\">\s*([^<]+)\s*</div>",
        desc,
        flags=re.I,
    )
    return [x.strip() for x in match.group(1).split(",") if x.strip()] if match else []


def extract_all_mpn_candidates(item: Mapping[str, Any]) -> list[str]:
    sources: list[str] = []
    for name in ("Manufacturer Part Number", "Interchange Part Number", "PartNumber", "OE/OEM Part Number"):
        if value := _aspect_value(item, name):
            sources.extend(parse_dirty_part_numbers(value))
    if item.get("mpn"):
        sources.append(str(item["mpn"]))
    for mpn in ((item.get("product") or {}).get("mpns") or []):
        sources.extend(parse_dirty_part_numbers(mpn))
    if item.get("subtitle") and looks_like_part_number(item.get("subtitle")):
        sources.extend(parse_dirty_part_numbers(item.get("subtitle")))
    for mpn in extract_interchange_part_numbers_from_description(item):
        sources.extend(parse_dirty_part_numbers(mpn))

    seen: set[str] = set()
    out: list[str] = []
    for value in sources:
        if value not in seen and looks_like_part_number(value):
            seen.add(value)
            out.append(value)
    return out


def is_title_token_hit(title: Any, target_mpn: str, target_mpn_loose: str) -> bool:
    if not title or not target_mpn or len(target_mpn) < 6:
        return False
    tokens = [normalize_mpn(x) for x in re.split(r"[,;/|\s]+", str(title)) if x]
    for i, token in enumerate(tokens):
        if token != target_mpn and not (target_mpn_loose and token == target_mpn_loose):
            continue
        if any(word in TITLE_BLOCK_WORDS for word in tokens[max(0, i - 3): i]):
            continue
        return True
    return False


def extract_compatibility_properties(item: Mapping[str, Any]) -> dict[str, Any]:
    # 适配: 只留车辆维度; 去掉 Universal Fitment / Performance Part (基本恒为 "No", 噪声)。
    keys = [
        "Compatible Makes", "Make", "Brand", "Model", "Fitment Type", "Placement on Vehicle", "Year",
        "Placement", "Compatibility", "Interchange Item Code",
    ]
    result = {key: value for key in keys if (value := _aspect_value(item, key))}
    if item.get("categoryPath"):
        result["categoryPath"] = item.get("categoryPath")
    return result


# 分类零件号: (输出 key, aspect 名) —— 前端按此顺序分行展示, 各自带标签。
CLASSIFIED_PN_ASPECTS: list[tuple[str, str]] = [
    ("oe", "OE/OEM Part Number"),
    ("mpn", "Manufacturer Part Number"),
    ("interchange", "Interchange Part Number"),
    ("superseded", "Superseded Part Number"),
]


def _clean_pn_list(raw_value: Any) -> list[str]:
    """清洗一个零件号 aspect 值: 去 "Xxx:" 前缀、按逗号/斜杠拆、按归一化去重 (保留展示原值)。

    只按逗号/分号/斜杠拆, 不按空格 —— "86612 M7200" 是一个带空格的号, 不能拆开。
    """
    s = str(raw_value or "").strip()
    if not s:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for part in re.split(r"[;,/]", s):
        part = re.sub(r"^\s*[A-Za-z][A-Za-z /]*:\s*", "", part).strip()  # 去 "Interchange:" 之类前缀
        if not part:
            continue
        key = normalize_mpn(part)  # 去空格/连字符 + 大写
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(part)
    return out


def extract_classified_part_numbers(item: Mapping[str, Any]) -> dict[str, list[str]]:
    """分类保留零件号 (带类型标签, 清洗去重), 供前端分行展示。
    不影响 extract_all_mpn_candidates / part_number_list 的匹配逻辑。"""
    out: dict[str, list[str]] = {}
    for key, aspect_name in CLASSIFIED_PN_ASPECTS:
        values = _clean_pn_list(_aspect_value(item, aspect_name))
        if values:
            out[key] = values
    return out


# 规格: 固定项 + 尺寸/规格类关键词 (aspect 名命中即收)。
SPEC_FIXED = ("Type", "Material", "Color", "Country of Origin", "Finish", "Genuine OEM")
SPEC_KEYWORDS = ("diameter", "bolt pattern", "length", "width", "height", "thickness",
                 "size", "number in pack", "voltage", "wattage")
# 已被别处消费的 aspect (号/适配/warranty), specs 不重复收。
_PN_ASPECT_NAMES = {a for _, a in CLASSIFIED_PN_ASPECTS} | {"PartNumber"}
_COMPAT_ASPECT_NAMES = {
    "Compatible Makes", "Make", "Brand", "Model", "Fitment Type", "Placement on Vehicle",
    "Year", "Placement", "Compatibility", "Interchange Item Code", "Universal Fitment", "Performance Part",
}
_WARRANTY_ASPECT_NAMES = {"Manufacturer Warranty", "Warranty"}


def extract_specs(item: Mapping[str, Any]) -> dict[str, str]:
    """规格结构: 固定项 + 名字命中尺寸/规格关键词的 aspect (有值才带)。号/适配/warranty 不收。"""
    out: dict[str, str] = {}
    for aspect in item.get("localizedAspects") or []:
        name = aspect.get("name")
        value = aspect.get("value")
        if not name or not value:
            continue
        if name in _PN_ASPECT_NAMES or name in _COMPAT_ASPECT_NAMES or name in _WARRANTY_ASPECT_NAMES:
            continue
        if name in SPEC_FIXED or any(kw in name.lower() for kw in SPEC_KEYWORDS):
            out[name] = value
    return out


def label_by_mpn(detail: Mapping[str, Any], target_mpn_raw: str) -> tuple[int | None, str, list[str], list[str]]:
    target = normalize_mpn(target_mpn_raw)
    target_loose = strip_capa_suffix(target)
    candidates = extract_all_mpn_candidates(detail)
    normalized = [normalize_mpn(x) for x in candidates]
    if not target:
        return None, "TARGET_MPN_EMPTY", candidates, normalized
    if target in normalized:
        return 1, "EXACT_MPN_MATCH", candidates, normalized
    if target_loose and any(strip_capa_suffix(x) == target_loose for x in normalized):
        return 1, "SUFFIX_TOLERANT_MATCH", candidates, normalized
    if is_title_token_hit(detail.get("title"), target, target_loose):
        return 1, "MPN_FOUND_IN_TITLE_TOKEN", candidates, normalized
    if not candidates:
        return None, "MPN_EMPTY_UNLABELED", candidates, normalized
    return 0, "MPN_PRESENT_NO_MATCH_NOISY_NEGATIVE", candidates, normalized


def looks_like_ccc_internal_number(raw: Any) -> bool:
    return bool(re.match(r"^\s*\d+\.\d+\s*$", str(raw or "")))
