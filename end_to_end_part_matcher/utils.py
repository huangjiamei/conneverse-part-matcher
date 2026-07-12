from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATEGORY_MAP = REPO_ROOT / "data/test_dataset/new_dataset_and_match_algorithm/part_desc_to_category.json"


def load_dotenv(path: Path = REPO_ROOT / ".env") -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def resolve_repo_path(path: str | Path) -> Path:
    p = Path(path)
    if p.is_absolute():
        return p
    direct = REPO_ROOT / p
    if direct.exists():
        return direct
    with_data = REPO_ROOT / "data" / p
    return with_data if with_data.exists() else direct


def normalize_mpn(raw: Any) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", str(raw or "")).upper()


MAKE_ABBREVIATION_MAP = {
    "HOND": "Honda", "TOYO": "Toyota", "BUIC": "Buick", "MAZD": "Mazda", "HYUN": "Hyundai",
    "CHEV": "Chevrolet", "FORD": "Ford", "NISS": "Nissan", "SUBA": "Subaru", "VW": "Volkswagen",
    "ACUR": "Acura", "LEXU": "Lexus", "INFI": "Infiniti", "KIA": "Kia", "GMC": "GMC",
    "DODG": "Dodge", "JEEP": "Jeep", "CHRY": "Chrysler", "MITS": "Mitsubishi", "AUDI": "Audi",
    "VOLV": "Volvo", "BMW": "BMW", "TESL": "Tesla", "RAM": "Ram", "BENZ": "Mercedes-Benz",
    "CADI": "Cadillac", "GENE": "Genesis", "SCIO": "Scion", "LINC": "Lincoln",
    "RANG": "Land Rover", "MASE": "Maserati", "ALFA": "Alfa Romeo",
}
AMBIGUOUS_SHORT_MODELS = {"Model", "Ioniq", "ID", "iX", "EQ", "RAV", "CX", "CT", "ES", "GX"}


def expand_make(raw_make: Any) -> str:
    value = str(raw_make or "").strip()
    return MAKE_ABBREVIATION_MAP.get(value.upper(), value)


def resolve_vehicle_model(vehicle: Mapping[str, Any]) -> str:
    raw_model = str(vehicle.get("model_guess") or "").strip()
    words = raw_model.split()
    if len(words) != 1 or words[0] not in AMBIGUOUS_SHORT_MODELS:
        return raw_model
    vehicle_raw = str(vehicle.get("vehicle_raw") or "")
    with_sep = re.search(rf"\b{re.escape(words[0])}(\s+|\.|-)([0-9A-Za-z][-\w.]{{0,4}})\b", vehicle_raw)
    if with_sep:
        return f"{words[0]}{with_sep.group(1)}{with_sep.group(2)}"
    no_sep = re.search(rf"\b{re.escape(words[0])}([0-9][\w]{{0,3}})\b", vehicle_raw)
    return f"{words[0]}{no_sep.group(1)}" if no_sep else raw_model


def normalize_source_part_info(data: Mapping[str, Any]) -> dict[str, Any]:
    source = data.get("source_part_info", data)  # accepts full dataset row too
    vehicle = dict(source.get("vehicle") or {})
    part_number = str(source.get("part_number") or "").strip()
    return {
        "vehicle": {
            "year": str(vehicle.get("year") or "").strip(),
            "make": expand_make(vehicle.get("make")),
            "model_guess": resolve_vehicle_model(vehicle),
            "vehicle_raw": str(vehicle.get("vehicle_raw") or "").strip(),
        },
        "part_description": str(source.get("part_description") or "").strip(),
        "part_type": source.get("part_type"),
        "part_number": part_number,
        "part_number_normalized": normalize_mpn(source.get("part_number_normalized") or part_number),
    }


def source_vehicle_text(source: Mapping[str, Any]) -> str:
    vehicle = source.get("vehicle") or {}
    raw = str(vehicle.get("vehicle_raw") or "").strip()
    canonical = " ".join(str(vehicle.get(k) or "").strip() for k in ("year", "make", "model_guess") if vehicle.get(k))
    return f"{canonical} {raw}".strip() if raw and canonical and canonical.lower() not in raw.lower() else (raw or canonical)


SHOP_ABBREVIATIONS = (
    (r"\bLT\b", "Left"), (r"\bRT\b", "Right"), (r"\bLH\b", "Left"), (r"\bRH\b", "Right"),
    (r"\bFR\b", "Front"), (r"\bRR\b", "Rear"), (r"\bw/o\b", "without"), (r"\bw/", "with "),
    (r"\bassy\b", "assembly"), (r"\bmldg\b", "molding"), (r"\bopng\b", "opening"),
    (r"\bbumpr\b", "bumper"), (r"\bbrkt\b", "bracket"),
)
CORE_PART_NOUNS = sorted([
    "lamp assembly", "door assembly", "quarter panel", "bumper cover", "headlamp assembly", "fender liner",
    "wheel opening", "impact bar", "fog lamp", "park sensor", "radiator support", "control arm", "mount bracket",
    "side cover", "tail lamp", "trim panel", "door panel", "outer cover", "inner cover", "wheelhouse liner",
    "wheel house", "body panel", "side panel", "trunk lid", "bumper", "fender", "mirror", "headlight",
    "taillight", "molding", "bracket", "grille", "hood", "cover", "liner", "support", "retainer", "nameplate",
    "clip", "shield", "rivet", "absorber", "hinge", "bezel", "seal", "deflector", "plate", "wheel", "sensor",
], key=len, reverse=True)


def clean_part_description(raw_desc: Any) -> str:
    desc = str(raw_desc or "")
    desc = re.sub(r"\([^)]*\)", " ", desc).replace(";", " ").replace(",", " ").replace('"', "")
    desc = re.sub(r"\bPart\s*#\s*\w+", " ", desc, flags=re.I)
    desc = re.sub(r"\bMODEL NOT PROVIDED\b|\bNOT PROVIDED\b|\bN/A\b", " ", desc, flags=re.I)
    desc = re.sub(r"\b\d+(\.\d+)?\s*(quarts?|gallons?|liters?|oz|lbs?|pints?)\b", " ", desc, flags=re.I)
    for pattern, replacement in SHOP_ABBREVIATIONS:
        desc = re.sub(pattern, replacement, desc, flags=re.I)
    return re.sub(r"\s+", " ", desc).strip()


def extract_core_keyword(cleaned_desc: str) -> str:
    lower = cleaned_desc.lower()
    for noun in CORE_PART_NOUNS:
        if noun in lower:
            return noun
    stem = re.sub(r"\b(with|without|w/o?)\b.*$", "", cleaned_desc, flags=re.I).strip()
    words = (stem or cleaned_desc).split()
    junk = {"left", "right", "front", "rear", "and", "or", "the", "model", "base"}
    meaningful = [w for w in words if len(w) >= 2 and not re.match(r"^[\d+\-.]+$", w.lower()) and w.lower() not in junk]
    return " ".join((meaningful or words)[-2:])


def _category_alias(text: Any) -> str:
    value = str(text or "").lower()
    value = re.sub(r"\([^)]*\)", " ", value)
    value = re.sub(r"[\"'#,;:]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _rough_category_core(text: Any) -> str:
    value = _category_alias(text)
    value = re.sub(r"\b(a/m|lt|rt|lh|rh|left|right|front|rear)\b", " ", value)
    value = re.split(r"\b(w/o|with|without|for|from|to)\b|,|;", value)[0]
    return re.sub(r"\s+", " ", value).strip()


class CategoryLookup:
    def __init__(self, path: Path = DEFAULT_CATEGORY_MAP) -> None:
        self.path = path
        self.descriptions: dict[str, dict[str, Any]] = {}
        self.alias_to_key: dict[str, str] = {}
        if not path.exists():
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        self.descriptions = {str(k): v for k, v in (data.get("descriptions") or {}).items() if isinstance(v, dict)}
        for key, info in self.descriptions.items():
            for alias in {_category_alias(key), _rough_category_core(key)}:
                self.alias_to_key[alias] = key
            for original in info.get("covered_by_original_descriptions") or []:
                for alias in {_category_alias(original), _rough_category_core(original)}:
                    self.alias_to_key[alias] = key

    def lookup(self, raw_description: Any) -> dict[str, Any] | None:
        for alias in (_category_alias(raw_description), _rough_category_core(raw_description)):
            key = self.alias_to_key.get(alias)
            if key:
                return self.descriptions[key]
        normalized = _category_alias(raw_description)
        matches = [k for k in self.descriptions if k and re.search(rf"\b{re.escape(k.lower())}\b", normalized)]
        return self.descriptions[max(matches, key=len)] if matches else None


@lru_cache(maxsize=8)
def get_category_lookup(path: Path = DEFAULT_CATEGORY_MAP) -> CategoryLookup:
    return CategoryLookup(resolve_repo_path(path).resolve())
