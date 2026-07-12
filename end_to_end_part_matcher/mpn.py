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
    keys = [
        "Compatible Makes", "Make", "Brand", "Model", "Fitment Type", "Placement on Vehicle", "Year",
        "Placement", "Compatibility", "Interchange Item Code", "Universal Fitment", "Performance Part",
    ]
    result = {key: value for key in keys if (value := _aspect_value(item, key))}
    if item.get("categoryPath"):
        result["categoryPath"] = item.get("categoryPath")
    return result


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
