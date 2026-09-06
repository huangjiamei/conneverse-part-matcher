"""checkCompatibility 适配闸 (Phase 2, 真删生效)。

只对 label=1 候选跑, 用车调 eBay checkCompatibility, 把明确 NOT_COMPATIBLE 的筛掉。
只减不加: 绝不用 COMPATIBLE 复活 label=0/None。

DB-free · model 级: matcher 无 VCdb, 所以只把 Model 对齐到 eBay 词表 (仅需 model_guess),
用 Year/Make/Model 判。
  - NOT_COMPATIBLE (model/年不在 listing ACES) = 跨车型硬阴性 → 可信。
  - UNDETERMINED (11504 需 Trim/Engine / 11505 没挂 ACES) / 对不上词表 / API 异常 → 保留 (fail-open)。
无 VCdb 不深究 trim/engine 级 NOT, 故比带 VCdb 的影子跑略保守 (只砍最确凿的跨车型错配)。

COMPAT_GATE_MODE 三档 (默认 strict):
  strict — NOT_COMPATIBLE 一律删。
  loose  — 只删"listing 标题/兼容属性里没出现查询 model"的 NOT (跨车型); 出现了 (同车型只差
           trim/年) → 保留、当 UNDETERMINED 处理。
  off    — 只标记不删 (= 影子模式, 回退用)。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Mapping

from .ebay import EbayApiError, EbayClient

logger = logging.getLogger(__name__)

VALID_MODES = {"strict", "loose", "off"}


def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def align_model(our_model: str, ebay_models: list[str]) -> str | None:
    """我方 model → eBay Model 词表值 (精确 > 前缀 > 包含); 对不上返回 None。"""
    m = _norm(our_model)
    if not m:
        return None
    for e in ebay_models:
        if _norm(e) == m:
            return e
    for e in ebay_models:
        if _norm(e).startswith(m + " "):
            return e
    for e in ebay_models:
        if m in _norm(e):
            return e
    return None


def _model_in_listing_text(model: str, candidate: Mapping[str, Any]) -> bool:
    """loose 判别: listing 标题 / 兼容属性文本里有没有出现查询 model。"""
    m = _norm(model)
    if not m:
        return False
    hay = _norm(candidate.get("title"))
    comp = candidate.get("compatibility") or {}
    for v in comp.values():
        hay += " " + _norm(v)
    return f" {m} " in f" {hay} "


def evaluate_candidate(
    ebay: EbayClient, vehicle: Mapping[str, Any], candidate: Mapping[str, Any],
    *, models_cache: dict[tuple[str, str, str], list[str]] | None = None,
) -> tuple[str, str]:
    """对一条候选跑 model 级 checkCompatibility。返回 (verdict, detail)。
    verdict ∈ COMPATIBLE / NOT_COMPATIBLE / UNDETERMINED。
    """
    year = str(vehicle.get("year") or "").strip()
    make = str(vehicle.get("make") or "").strip()
    model = str(vehicle.get("model_guess") or "").strip()
    item_id = candidate.get("item_id")
    category_id = candidate.get("category_id")
    if not (year and make and model and item_id):
        return "UNDETERMINED", "missing_vehicle_or_item"
    if not category_id:
        return "UNDETERMINED", "no_categoryId"

    cache_key = (str(category_id), year, make)
    ebay_models: list[str] | None = models_cache.get(cache_key) if models_cache is not None else None
    if ebay_models is None:
        try:
            ebay_models = ebay.get_compatibility_property_values(
                category_id=str(category_id), compatibility_property="Model",
                filter_dict={"Year": year, "Make": make},
            )
        except EbayApiError:
            return "UNDETERMINED", "taxonomy_error"
        if models_cache is not None:
            models_cache[cache_key] = ebay_models

    aligned = align_model(model, ebay_models)
    if not aligned:
        return "UNDETERMINED", "model_not_in_ebay"

    try:
        res = ebay.check_compatibility(
            item_id=str(item_id),
            compatibility_properties=[
                {"name": "Year", "value": year},
                {"name": "Make", "value": make},
                {"name": "Model", "value": aligned},
            ],
        )
    except EbayApiError:
        # 11505 (没挂 ACES) / 404 (item 没了) 等都走这里 → 保留
        return "UNDETERMINED", "check_error"

    status = res.get("compatibilityStatus") or "UNDETERMINED"
    if status == "COMPATIBLE":
        return "COMPATIBLE", "model_compat"
    if status == "NOT_COMPATIBLE":
        return "NOT_COMPATIBLE", "model_not"
    return "UNDETERMINED", "model_undetermined"  # 11504 需 trim/engine, 无 DB 不深究


def apply_compat_gate(
    source: Mapping[str, Any], candidates: list[dict[str, Any]], *,
    ebay: EbayClient, mode: str = "strict",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """在 label=1 候选上跑适配闸。返回 (保留的候选, 被删的候选, 统计)。

    所有模式都会给每条 label=1 候选打 ``compat_verdict`` (+ ``compat_gate`` 细节)。
    strict/loose 会把命中删除规则的从"保留"里移出; off 只标记不删。
    """
    mode = mode if mode in VALID_MODES else "strict"
    vehicle = source.get("vehicle") or {}
    stats: dict[str, Any] = {
        "mode": mode, "evaluated": 0, "compatible": 0, "not_compatible": 0,
        "undetermined": 0, "filtered": 0, "kept_not_loose": 0, "removed_item_ids": [],
    }
    models_cache: dict[tuple[str, str, str], list[str]] = {}
    kept: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []

    for c in candidates:
        if c.get("candidate_label") != 1:
            kept.append(c)
            continue

        verdict, detail = evaluate_candidate(ebay, vehicle, c, models_cache=models_cache)
        stats["evaluated"] += 1
        stats[{"COMPATIBLE": "compatible", "NOT_COMPATIBLE": "not_compatible"}.get(verdict, "undetermined")] += 1

        would_filter = False
        rule: str | None = None
        if verdict == "NOT_COMPATIBLE":
            if mode == "strict":
                would_filter, rule = True, "strict_not"
            elif mode == "loose":
                if _model_in_listing_text(vehicle.get("model_guess"), c):
                    rule = "loose_same_model_kept"          # 同车型只差 trim/年 → 当未定保留
                    verdict = "UNDETERMINED"
                    stats["kept_not_loose"] += 1
                else:
                    would_filter, rule = True, "loose_cross_vehicle"
            # off: 从不删

        c["compat_verdict"] = verdict
        c["compat_gate"] = {"verdict": verdict, "detail": detail, "mode": mode,
                            "would_filter": would_filter, "rule": rule}

        if would_filter:
            removed.append(c)
            stats["filtered"] += 1
            stats["removed_item_ids"].append(c.get("item_id"))
            logger.info(
                "compat_gate[%s] FILTER item=%s veh=%s %s %s | verdict=%s rule=%s | title=%s",
                mode, c.get("item_id"), vehicle.get("year"), vehicle.get("make"),
                vehicle.get("model_guess"), detail, rule, (c.get("title") or "")[:80],
            )
        else:
            kept.append(c)

    return kept, removed, stats
