from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .ebay import EbayApiError, EbayClient
from .mpn import extract_compatibility_properties, label_by_mpn, looks_like_ccc_internal_number
from .semantics import OpenAISemanticJudge, apply_ngram_and_llm
from .utils import CategoryLookup, DEFAULT_CATEGORY_MAP, get_category_lookup, normalize_source_part_info, resolve_repo_path


@dataclass(frozen=True)
class PipelineConfig:
    search_limit: int = 5
    candidate_limit: int = 15
    request_delay_seconds: float = 0.0
    category_map_path: Path = DEFAULT_CATEGORY_MAP
    use_llm: bool = True
    llm_model: str = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    llm_min_positive_confidence: float = 0.85


def match_source_part(
    source_part_info: Mapping[str, Any],
    *,
    config: PipelineConfig | None = None,
    ebay_client: EbayClient | None = None,
    llm_judge: OpenAISemanticJudge | None = None,
) -> dict[str, Any]:
    """Run retrieval -> MPN match -> n-gram filter -> optional LLM for one source part.

    Input can be exactly ``source_part_info`` from test_dataset_v4.json or a full
    dataset row containing that key. Output keeps the dataset row shape so it can
    be inspected alongside the existing generated JSONL rows.
    """

    config = config or PipelineConfig()
    source = normalize_source_part_info(source_part_info)
    ebay = ebay_client or EbayClient()
    category_lookup = get_category_lookup(config.category_map_path)

    items, tried_levels, category_info = search_candidates(source, ebay=ebay, category_lookup=category_lookup, config=config)
    candidates, detail_errors, match_rank = fetch_and_label_candidates(source, items, ebay=ebay, config=config)
    stage_counts = apply_ngram_and_llm(
        source,
        candidates,
        use_llm=config.use_llm,
        judge=llm_judge,
        judge_factory=lambda: OpenAISemanticJudge(
            model=config.llm_model,
            min_positive_confidence=config.llm_min_positive_confidence,
        ),
    )
    label, label_source = summarize_record_label(candidates)
    final_positive_count = sum(1 for c in candidates if c.get("candidate_label") == 1)
    mpn_positive_count = sum(1 for c in candidates if _has_mpn_positive_source(c))
    last_level = tried_levels[-1] if tried_levels else {}
    successful_searches = [
        meta for meta in tried_levels
        if isinstance(meta.get("resultCount"), int) and meta.get("resultCount", 0) > 0
    ]
    queries_used = [{"level": meta.get("level"), "query": meta.get("query")} for meta in successful_searches]
    merged_levels_used = len(successful_searches) > 1
    effective_level = successful_searches[-1] if successful_searches else last_level

    return {
        "source_part_info": source,
        "candidate_info_list": candidates,
        "label": label,
        "label_source": label_source,
        "dataset_meta": {
            "constructed_at": datetime.now(timezone.utc).isoformat(),
            "query_used": None if merged_levels_used else effective_level.get("query"),
            "level_used": "merged" if merged_levels_used else effective_level.get("level"),
            "queries_used": queries_used,
            "candidate_hit_count": final_positive_count,
            "candidate_hit_ratio": round(final_positive_count / len(candidates), 4) if candidates else 0,
            "mpn_candidate_hit_count": mpn_positive_count,
            "match_rank": match_rank,
            "tried_levels": tried_levels,
            "category_id_used": (category_info or {}).get("category_id"),
            "supports_compat_used": bool((category_info or {}).get("supports_compat")),
            "detail_errors": detail_errors,
            "post_mpn_stage_counts": stage_counts,
        },
    }


def search_candidates(
    source: Mapping[str, Any],
    *,
    ebay: EbayClient,
    category_lookup: CategoryLookup,
    config: PipelineConfig,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None]:
    vehicle = source.get("vehicle") or {}
    part_description = str(source.get("part_description") or "")
    target_mpn_raw = str(source.get("part_number") or "")
    category_info = category_lookup.lookup(part_description)
    category_id = category_info.get("category_id") if category_info else None
    tried: list[dict[str, Any]] = []
    result_sets: list[dict[str, Any]] = []
    per_source_limit = max(1, min(config.search_limit, 5))
    candidate_limit = max(1, min(config.candidate_limit, 15))

    if target_mpn_raw and len(target_mpn_raw) >= 3 and not looks_like_ccc_internal_number(target_mpn_raw):
        try:
            result = ebay.search_by_part_number(part_number=target_mpn_raw, limit=per_source_limit, category_id=category_id)
            tried.append(result["searchMeta"])
            result_sets.append(result)
        except EbayApiError as exc:
            if exc.fatal or exc.status in {401, 403}:
                raise
            tried.append({"level": "mpn", "query": target_mpn_raw, "resultCount": "error", "error": str(exc)})

    has_vehicle_context = all(vehicle.get(k) for k in ("year", "make", "model_guess"))
    if category_info and category_info.get("supports_compat") and has_vehicle_context:
        compat_filter = f"Year:{vehicle['year']};Make:{vehicle['make']};Model:{vehicle['model_guess']}"
        try:
            result = ebay.search_by_compatibility(
                query=part_description,
                category_id=str(category_info["category_id"]),
                compatibility_filter=compat_filter,
                limit=per_source_limit,
            )
            tried.append(result["searchMeta"])
            result_sets.append(result)
        except EbayApiError as exc:
            if exc.fatal or exc.status in {401, 403}:
                raise
            tried.append({"level": "compat", "query": part_description, "resultCount": "error", "error": str(exc)})

    try:
        result = ebay.search_by_keyword(
            vehicle_year=str(vehicle.get("year") or ""),
            vehicle_make=str(vehicle.get("make") or ""),
            vehicle_model=str(vehicle.get("model_guess") or ""),
            part_description=part_description,
            limit=per_source_limit,
            category_id=category_id,
        )
        tried.append(result["searchMeta"])
        result_sets.append(result)
    except EbayApiError as exc:
        if exc.fatal or exc.status in {401, 403}:
            raise
        tried.append({"level": "keyword", "query": part_description, "resultCount": "error", "error": str(exc)})

    merged: list[dict[str, Any]] = []
    items_by_id: dict[str, dict[str, Any]] = {}
    # This is exact listing-level retrieval dedupe. Catalog Part identity across
    # different eBay itemIds requires normalized detail/offer aggregation later.
    for result_set in result_sets:
        search_meta = result_set.get("searchMeta") or {}
        source_query = {"level": search_meta.get("level"), "query": search_meta.get("query")}
        for raw_item in (result_set.get("items") or [])[:per_source_limit]:
            item = dict(raw_item)
            item_id = str(item.get("itemId") or "")
            if item_id and item_id in items_by_id:
                existing_sources = items_by_id[item_id]["source_queries"]
                if source_query not in existing_sources:
                    existing_sources.append(source_query)
                continue
            item["source_queries"] = [source_query]
            merged.append(item)
            if item_id:
                items_by_id[item_id] = item

    return merged[:candidate_limit], tried, category_info


def fetch_and_label_candidates(
    source: Mapping[str, Any],
    items: Iterable[Mapping[str, Any]],
    *,
    ebay: EbayClient,
    config: PipelineConfig,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int | None]:
    candidates: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    match_rank: int | None = None
    for rank, item in enumerate(items, 1):
        item_id = item.get("itemId")
        if not item_id:
            continue
        try:
            detail = ebay.get_item(str(item_id))
        except EbayApiError as exc:
            errors.append({"item_id": item_id, "error": str(exc), "status": exc.status})
            _sleep(config.request_delay_seconds)
            continue
        candidate = build_candidate_info(detail, target_mpn_raw=str(source.get("part_number") or ""))
        candidate["source_queries"] = list(item.get("source_queries") or [])
        candidates.append(candidate)
        if candidate.get("candidate_label") == 1 and match_rank is None:
            match_rank = rank
        _sleep(config.request_delay_seconds)
    return candidates, errors, match_rank


def build_candidate_info(detail: Mapping[str, Any], *, target_mpn_raw: str) -> dict[str, Any]:
    candidate_label, candidate_label_source, mpns, normalized_mpns = label_by_mpn(detail, target_mpn_raw)

    # ---------- Optimizer 需要的字段 (从 raw detail 提取) ----------
    # 都是浅拷贝, 不做归一化/单位换算 —— optimizer/adapter 层负责解释
    seller = detail.get("seller") or {}
    avails = detail.get("estimatedAvailabilities") or []
    availability = avails[0] if avails else {}
    shipping_opts = detail.get("shippingOptions") or []
    shipping = shipping_opts[0] if shipping_opts else {}
    returns = detail.get("returnTerms") or {}
    return_period = returns.get("returnPeriod") or {}
    location = detail.get("itemLocation") or {}

    # warranty 从 aspects 提取 raw 值 (归一化在 optimizer 侧)
    warranty_raw = None
    for aspect in (detail.get("localizedAspects") or []):
        if aspect.get("name") in ("Manufacturer Warranty", "Warranty"):
            warranty_raw = aspect.get("value")
            break

    optimizer_fields = {
        # 卖家信誉
        "seller_username": seller.get("username"),
        "seller_feedback_pct": seller.get("feedbackPercentage"),
        "seller_feedback_count": seller.get("feedbackScore"),
        "top_rated": detail.get("topRatedBuyingExperience"),
        # 库存
        "availability_status": availability.get("estimatedAvailabilityStatus"),
        "available_qty": availability.get("estimatedAvailableQuantity"),
        "sold_qty": availability.get("estimatedSoldQuantity"),
        # 交付
        "shipping_cost": (shipping.get("shippingCost") or {}).get("value"),
        "delivery_min_date": shipping.get("minEstimatedDeliveryDate"),
        "delivery_max_date": shipping.get("maxEstimatedDeliveryDate"),
        # 保障
        "returns_accepted": returns.get("returnsAccepted"),
        "return_period_days": return_period.get("value") if return_period.get("unit") == "CALENDAR_DAY" else None,
        "warranty_raw": warranty_raw,
        # 地理
        "country": location.get("country"),
    }

    return {
        "title": detail.get("title") or "",
        "subtitle": detail.get("subtitle") or "",
        "part_number_list": mpns,
        "part_number_list_normalized": normalized_mpns,
        "compatibility": extract_compatibility_properties(detail),
        "condition": detail.get("condition") or "",
        "item_id": detail.get("itemId"),
        "item_web_url": detail.get("itemWebUrl"),
        "price": detail.get("price"),
        "candidate_label": candidate_label,
        "candidate_label_source": candidate_label_source,
        # 新增: optimizer 用字段, 集中在一个 dict 下, 现有前端/调用方不受影响
        "optimizer_fields": optimizer_fields,
    }


def summarize_record_label(candidates: Iterable[Mapping[str, Any]]) -> tuple[int | None, str]:
    candidate_list = list(candidates)
    labels = [candidate.get("candidate_label") for candidate in candidate_list]
    if any(label == 1 for label in labels):
        source = next((str(c.get("candidate_label_source")) for c in candidate_list if c.get("candidate_label") == 1), "POSITIVE_CANDIDATE_FOUND")
        return 1, source
    if not labels:
        return None, "NO_CANDIDATES"

    unresolved_review = any(
        c.get("needs_llm_review")
        and (c.get("llm_semantic_judgement") or {}).get("status") in {"skipped", "error"}
        for c in candidate_list
    )
    if unresolved_review or any(label is None for label in labels):
        return None, "UNRESOLVED_CANDIDATES_NEED_LLM_REVIEW"
    if all(label == 0 for label in labels):
        return 0, "ALL_CANDIDATES_REJECTED_AFTER_MPN_NGRAM_LLM"
    return None, "UNRESOLVED_CANDIDATES_NEED_REVIEW"


def _has_mpn_positive_source(candidate: Mapping[str, Any]) -> bool:
    positive_sources = {"EXACT_MPN_MATCH", "SUFFIX_TOLERANT_MATCH", "MPN_FOUND_IN_TITLE_TOKEN"}
    return candidate.get("candidate_label_source") in positive_sources or candidate.get("candidate_label_source_previous") in positive_sources


def _sleep(seconds: float) -> None:
    if seconds:
        time.sleep(seconds)


def read_json_or_jsonl_record(path: Path, *, line_no: int) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8-sig")
    stripped = text.lstrip()
    if stripped.startswith("["):
        return json.loads(text)[line_no - 1]
    if stripped.startswith("{") and "\n" not in stripped.rstrip():
        return json.loads(text)
    for current_line, line in enumerate(text.splitlines(), 1):
        if current_line == line_no:
            return json.loads(line)
    raise ValueError(f"{path} does not have line {line_no}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the single source_part_info matching pipeline.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source-json", help="JSON object: either source_part_info or a full dataset row")
    source.add_argument("--input", type=Path, help="JSON/JSONL file containing source_part_info rows")
    parser.add_argument("--line-no", type=int, default=1, help="1-based JSONL line or JSON-array index for --input")
    parser.add_argument("--output", type=Path, default=None, help="Optional output JSON path")
    parser.add_argument("--category-map", type=Path, default=DEFAULT_CATEGORY_MAP)
    parser.add_argument("--search-limit", type=int, default=5, help="Per-source candidate limit (hard maximum: 5)")
    parser.add_argument("--candidate-limit", type=int, default=15, help="Merged candidate limit (hard maximum: 15)")
    parser.add_argument("--request-delay", type=float, default=0.0, help="Optional delay between eBay detail requests")
    parser.add_argument("--no-llm", action="store_true", help="Do not call the LLM for n-gram review cases")
    parser.add_argument("--llm-model", default=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"))
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    source = json.loads(args.source_json) if args.source_json else read_json_or_jsonl_record(resolve_repo_path(args.input), line_no=args.line_no)
    config = PipelineConfig(
        search_limit=args.search_limit,
        candidate_limit=args.candidate_limit,
        request_delay_seconds=args.request_delay,
        category_map_path=resolve_repo_path(args.category_map),
        use_llm=not args.no_llm,
        llm_model=args.llm_model,
    )
    record = match_source_part(source, config=config)
    output = json.dumps(record, ensure_ascii=False, indent=2 if args.pretty else None)
    if args.output:
        output_path = resolve_repo_path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
