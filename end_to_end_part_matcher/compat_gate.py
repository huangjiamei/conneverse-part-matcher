"""checkCompatibility 适配闸 (Phase 2, 真删生效)。

只对 label=1 候选跑, 用车调 eBay checkCompatibility, 把明确 NOT_COMPATIBLE 的筛掉。
只减不加: 绝不用 COMPATIBLE 复活 label=0/None。

DB-free · model 级: matcher 无 VCdb, 所以只把 Model 对齐到 eBay 词表 (仅需 model_guess),
用 Year/Make/Model 判。
  - NOT_COMPATIBLE (model/年不在 listing ACES) = 跨车型硬阴性 → 可信。
  - UNDETERMINED (11504 需 Trim/Engine / 11505 没挂 ACES) / 对不上词表 / API 异常 → 保留 (fail-open)。
无 VCdb 不深究 trim/engine 级 NOT, 故比带 VCdb 的影子跑略保守 (只砍最确凿的跨车型错配)。

COMPAT_GATE_MODE 三档 (默认 strict, 管 **model 级** 删除层):
  strict — NOT_COMPATIBLE 一律删。
  loose  — 只删"listing 标题/兼容属性里没出现查询 model"的 NOT (跨车型); 出现了 (同车型只差
           trim/年) → 保留、当 UNDETERMINED 处理。
  off    — 只标记不删 (= 影子模式, 回退用)。

COMPAT_ENGINE_LEVEL 两档 (默认 shadow, 管 **engine/trim 级** NOT 删不删):
  shadow  — engine/trim 级 NOT 只记不删 (退回 UNDETERMINED + ``*_shadow`` detail)。量化(40 条)
            发现引擎级硬删误杀高 (引擎无关件被引擎删、ACES 粗粒度 listing 被 trim+engine 假阴),
            故先止血: 净删除行为 == model 级 (Phase 2 之前)。
  enforce — engine/trim 级 NOT 参与删除 (= Phase 2 行为)。护栏 (同 listing 换发动机能 COMPATIBLE
            才信其 NOT + 类目限定 + 标题守卫) 做好后再切。
model 级 (Y/M/M) 的 NOT 两档都照删 —— 带不带 engine 都先判 model 级, 不受本开关影响。
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Mapping

from .ebay import EbayApiError, EbayClient

logger = logging.getLogger(__name__)

VALID_MODES = {"strict", "loose", "off"}  # model 级删除层 (COMPAT_GATE_MODE)

# 引擎/trim 级 NOT 的处理档 (COMPAT_ENGINE_LEVEL):
#   shadow  — engine/trim 级 NOT 只记不删 (默认; 量化发现其误杀高, 先止血)。
#   enforce — engine/trim 级 NOT 参与删除 (= Phase 2 行为; 护栏做好后再切)。
ENGINE_LEVELS = {"shadow", "enforce"}


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


# ------------------------------------------------------------------
# engine / trim 对齐 (镜像 align_model, 但按 spike 的三条精度修正来写)
# ------------------------------------------------------------------

def _canonical_aspiration(s: str) -> str | None:
    """把进气归一成一个可跨词表匹配的 token: turbo / supercharg / natural。
    容忍 "Twin Turbo" / "Turbocharged" 都归 turbo; "Naturally Aspirated" → natural。"""
    if "turbo" in s:
        return "turbo"
    if "supercharg" in s:
        return "supercharg"
    if "natural" in s:
        return "natural"
    return None


def _parse_engine(engine_str: str) -> dict[str, str | None]:
    """把我方 VCdb engine 人读串 (如 "2.0L L4 GAS Naturally Aspirated") 拆成匹配 token。
    修正#1: 带上 aspiration —— 否则同排量 NA↔Turbo 会互串。"""
    s = engine_str.lower()
    toks = s.split()
    liter = None
    m = re.match(r"^(\d+(?:\.\d+)?)l$", toks[0]) if toks else None
    if m:
        liter = m.group(1) + "l"  # "2.0l"
    # block+缸数一般是第 2 个 token (如 l4 / v8); 没有就跳过 (blockType 缺失时)
    blockcyl = None
    if len(toks) >= 2 and re.match(r"^[a-z]\d{1,2}$", toks[1]):
        blockcyl = toks[1]  # "v8" / "l4"
    fuel = "diesel" if "diesel" in s else "flex" if "flex" in s else "gas" if "gas" in s else None
    return {"liter": liter, "blockcyl": blockcyl, "fuel": fuel, "asp": _canonical_aspiration(s)}


def align_engine(our_engine: str, ebay_engines: list[str]) -> list[str]:
    """我方 engine 串 → eBay Engine 词表值 (可能多条; 通常唯一)。
    按 liter + 缸数 + 燃料 + aspiration 全部为子串才算命中 (eBay 串很啰嗦, 容忍 CC/CID/OHV 等)。
    """
    p = _parse_engine(our_engine)
    toks = [t for t in (p["liter"], p["blockcyl"], p["fuel"], p["asp"]) if t]
    if not toks:
        return []
    return [e for e in ebay_engines if all(t in e.lower() for t in toks)]


# eBay Trim 词表值 = "{trim} {车身} {N-Door}"。剥掉车身/门数后, 剩下的才是 trim 码。
# 修正#2: 用"剥车身后精确相等", 不用前缀 —— 否则 EX 串 EX-L、Sport 串 Sport S。
_BODY_SUFFIXES = sorted(
    [
        "crew cab pickup", "extended cab pickup", "standard cab pickup", "club cab pickup",
        "quad cab pickup", "mega cab pickup", "cab pickup",
        "sport utility", "sport van", "cargo van", "passenger van", "mini cargo van",
        "mini passenger van", "sedan", "coupe", "hatchback", "convertible", "wagon",
        "roadster", "minivan", "van", "pickup", "targa", "hardtop", "liftback", "fastback",
        "notchback",
    ],
    key=len,
    reverse=True,  # 先剥最长的 (crew cab pickup 早于 pickup)
)


def _norm_trim(s: Any) -> str:
    """小写, 保留连字符 (EX-L / 4-door 不拆), 其余非字母数字转空格并压缩。"""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9-]+", " ", str(s or "").lower())).strip()


def _trim_code(ebay_trim: str) -> str:
    """从 eBay Trim 值里剥掉尾部 N-Door + 车身短语, 得到 trim 码。"""
    t = _norm_trim(ebay_trim)
    t = re.sub(r"\s*\b\d+-door$", "", t).strip()  # 去尾部 "4-door"
    changed = True
    while changed and t:
        changed = False
        for b in _BODY_SUFFIXES:
            if t.endswith(" " + b):
                t = t[: -len(" " + b)].strip()
                changed = True
                break
    return t


def align_trim(sub_model: str, ebay_trims: list[str]) -> list[str]:
    """我方 sub_model → eBay Trim 词表值 (同一 trim 的所有车身变体都算命中)。
    剥掉车身后 trim 码需与 sub_model 归一化后**精确相等**。"""
    sm = _norm_trim(sub_model)
    if not sm:
        return []
    return [t for t in ebay_trims if _trim_code(t) == sm]


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


def _check_status(ebay: EbayClient, item_id: Any, props: list[dict[str, str]]) -> str:
    """发一次 checkCompatibility, 返回 status 串。API 异常 (11505 没挂 ACES / 404 等)
    → UNDETERMINED, 保留 (fail-open), 绝不当成 NOT 去删。"""
    try:
        res = ebay.check_compatibility(item_id=str(item_id), compatibility_properties=props)
    except EbayApiError:
        return "UNDETERMINED"
    return res.get("compatibilityStatus") or "UNDETERMINED"


def _legal_values(
    ebay: EbayClient, category_id: Any, prop: str, filt: dict[str, str], cache: dict[Any, Any]
) -> list[str] | None:
    """拉某 category 下某属性 (Model/Engine/Trim) 的 eBay 合法值 (逐候选类目), 结果缓存。
    API 异常 → None (调用方据此 fail-open)。"""
    key = (str(category_id), prop, tuple(sorted(filt.items())))
    if key in cache:
        return cache[key]
    try:
        vals: list[str] | None = ebay.get_compatibility_property_values(
            category_id=str(category_id), compatibility_property=prop, filter_dict=filt
        )
    except EbayApiError:
        vals = None
    cache[key] = vals
    return vals


def evaluate_candidate(
    ebay: EbayClient, vehicle: Mapping[str, Any], candidate: Mapping[str, Any],
    *, values_cache: dict[Any, Any] | None = None, engine_level: str = "shadow",
) -> tuple[str, str]:
    """对一条候选跑 checkCompatibility。返回 (verdict, detail)。verdict ∈ COMPATIBLE / NOT_COMPATIBLE / UNDETERMINED。

    engine_level (默认 shadow):
      shadow  — engine/trim 级 NOT **不删** (返回 UNDETERMINED + ``*_shadow`` detail, 只观察)。
      enforce — engine/trim 级 NOT 参与删除 (= Phase 2 行为)。
    model 级 (Y/M/M) 的 NOT 两档都照删 —— 那是安全的跨车型阴性, 不受本开关影响。

    请求无 engine → model 级行为不变。engine 对不上 eBay 词表 → 回落 model 级 (其 NOT 属 model 级, 照删)。
    """
    # engine/trim 级 NOT 的收口: enforce 才真删; shadow 只记不删 (退回 UNDETERMINED)。
    def _eng_not(detail: str) -> tuple[str, str]:
        if engine_level == "enforce":
            return "NOT_COMPATIBLE", detail
        return "UNDETERMINED", detail + "_shadow"

    year = str(vehicle.get("year") or "").strip()
    make = str(vehicle.get("make") or "").strip()
    model = str(vehicle.get("model_guess") or "").strip()
    engine = str(vehicle.get("engine") or "").strip()
    sub_model = str(vehicle.get("sub_model") or "").strip()
    item_id = candidate.get("item_id")
    category_id = candidate.get("category_id")
    if not (year and make and model and item_id):
        return "UNDETERMINED", "missing_vehicle_or_item"
    if not category_id:
        return "UNDETERMINED", "no_categoryId"

    cache = values_cache if values_cache is not None else {}

    # 1. Model 对齐 (和原来一致)
    ebay_models = _legal_values(ebay, category_id, "Model", {"Year": year, "Make": make}, cache)
    if ebay_models is None:
        return "UNDETERMINED", "taxonomy_error"
    aligned_model = align_model(model, ebay_models)
    if not aligned_model:
        return "UNDETERMINED", "model_not_in_ebay"

    base_props = [
        {"name": "Year", "value": year},
        {"name": "Make", "value": make},
        {"name": "Model", "value": aligned_model},
    ]

    # 2. model 级 (Y/M/M) 永远先判 —— 跨车型阴性, 安全, 两档都照删; 也是 shadow 下唯一的删除依据。
    #    (放在 engine 之前, 才能保证"带 engine 时 model 级 NOT 仍被删", 净删除行为 == model 级。)
    model_st = _check_status(ebay, item_id, base_props)
    if model_st == "NOT_COMPATIBLE":
        return "NOT_COMPATIBLE", "model_not"
    model_verdict = "COMPATIBLE" if model_st == "COMPATIBLE" else "UNDETERMINED"
    model_detail = "model_compat" if model_st == "COMPATIBLE" else "model_undetermined"

    # 无 engine → 到 model 级为止 (原行为)
    if not engine:
        return model_verdict, model_detail

    # 3. 有 engine → 逐候选类目对齐 engine (只发用户选的那个 engine 的对齐值)
    ebay_engines = _legal_values(ebay, category_id, "Engine", {"Year": year, "Make": make, "Model": aligned_model}, cache)
    aligned_engines = align_engine(engine, ebay_engines) if ebay_engines else []
    if not aligned_engines:
        # engine 没对上 eBay 词表 → 就用上面的 model 级结果 (其 NOT 已删过)
        return model_verdict, "engine_unaligned_" + model_detail

    # 3a. engine 级判定 (observation: shadow 不删只记, enforce 才删)
    eng_statuses = []
    for ev in aligned_engines:
        st = _check_status(ebay, item_id, base_props + [{"name": "Engine", "value": ev}])
        if st == "COMPATIBLE":
            return "COMPATIBLE", "engine_compat"
        eng_statuses.append(st)
    if eng_statuses and all(s == "NOT_COMPATIBLE" for s in eng_statuses):
        return _eng_not("engine_not")  # 默认 shadow: 只记不删 (量化发现引擎级 NOT 误杀高)

    # 3b. engine 仍 UNDETERMINED (11504) → 对齐 trim 逐个补发 Y/M/M+Trim+Engine
    if sub_model:
        ebay_trims = _legal_values(ebay, category_id, "Trim", {"Year": year, "Make": make, "Model": aligned_model}, cache)
        aligned_trims = align_trim(sub_model, ebay_trims) if ebay_trims else []
        if aligned_trims:
            eng_for_trim = aligned_engines[0]
            trim_statuses = []
            for tv in aligned_trims:
                st = _check_status(ebay, item_id, base_props + [
                    {"name": "Trim", "value": tv}, {"name": "Engine", "value": eng_for_trim},
                ])
                if st == "COMPATIBLE":
                    return "COMPATIBLE", "trim_engine_compat"  # UNDETERMINED 靠 trim+engine 救活
                trim_statuses.append(st)
            if trim_statuses and all(s == "NOT_COMPATIBLE" for s in trim_statuses):
                return _eng_not("trim_engine_not")  # 默认 shadow: 只记不删 (ACES 粗粒度会假阴)

    return model_verdict, "engine_undetermined"  # 仍未定 → fail-open 保留 (沿用 model 级判定)


def apply_compat_gate(
    source: Mapping[str, Any], candidates: list[dict[str, Any]], *,
    ebay: EbayClient, mode: str = "strict", engine_level: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """在 label=1 候选上跑适配闸。返回 (保留的候选, 被删的候选, 统计)。

    所有模式都会给每条 label=1 候选打 ``compat_verdict`` (+ ``compat_gate`` 细节)。
    strict/loose 会把命中删除规则的从"保留"里移出; off 只标记不删。

    engine_level (默认取 env COMPAT_ENGINE_LEVEL, 缺省 shadow): 控制 engine/trim 级 NOT 删不删。
    shadow 时那些 NOT 退回 UNDETERMINED (只记不删), 净删除行为 == model 级 (Phase 2 之前)。
    """
    mode = mode if mode in VALID_MODES else "strict"
    engine_level = engine_level or os.getenv("COMPAT_ENGINE_LEVEL", "shadow")
    engine_level = engine_level if engine_level in ENGINE_LEVELS else "shadow"
    vehicle = source.get("vehicle") or {}
    stats: dict[str, Any] = {
        "mode": mode, "engine_level": engine_level,
        "evaluated": 0, "compatible": 0, "not_compatible": 0,
        "undetermined": 0, "filtered": 0, "kept_not_loose": 0, "removed_item_ids": [],
        # shadow 观察: engine/trim 级本"会删"但被 shadow 拦下的条数 + itemId (留作护栏分析)
        "shadow_engine_trim_not": 0, "shadow_not_item_ids": [],
    }
    # 逐候选类目缓存 Model/Engine/Trim 的 eBay 合法值 (key 含 category+prop+filter)
    values_cache: dict[Any, Any] = {}
    kept: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []

    for c in candidates:
        if c.get("candidate_label") != 1:
            kept.append(c)
            continue

        verdict, detail = evaluate_candidate(
            ebay, vehicle, c, values_cache=values_cache, engine_level=engine_level
        )
        stats["evaluated"] += 1
        stats[{"COMPATIBLE": "compatible", "NOT_COMPATIBLE": "not_compatible"}.get(verdict, "undetermined")] += 1
        # shadow 拦下的 engine/trim NOT (verdict 已是 UNDETERMINED, detail 带 _shadow) → 计数观察
        if detail in ("engine_not_shadow", "trim_engine_not_shadow"):
            stats["shadow_engine_trim_not"] += 1
            stats["shadow_not_item_ids"].append(c.get("item_id"))

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
