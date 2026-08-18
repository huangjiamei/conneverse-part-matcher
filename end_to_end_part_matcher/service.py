"""FastAPI HTTP service wrapping the matcher pipeline.

Run with:
    uvicorn end_to_end_part_matcher.service:app --host 0.0.0.0 --port 8001 --reload
"""

from __future__ import annotations

import logging
import os
from typing import Any, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .pipeline import PipelineConfig, match_source_part
from .utils import normalize_delivery_zip
from .taxonomy import (
    DEFAULT_ROOT_CATEGORY_ID,
    MOTORS_CATEGORY_TREE_ID,
    fetch_taxonomy,
)

from algorithms.optimizer import (
    build_candidate_from_matcher,
    optimize,
    PRESETS,
)

DEFAULT_PRESET = "Budget"

# uvicorn 只给自己的 logger 装 handler, root 是空的; 不配置的话 pipeline 里
# 那句 "compat: using category X from ..." 看不到。
logging.basicConfig(
    level=os.getenv("MATCHER_LOG_LEVEL", "INFO"),
    format="%(levelname)s %(name)s: %(message)s",
)


app = FastAPI(
    title="Conneverse Part Matcher",
    version="0.3.0",
    description="eBay retrieval + MPN labeling + n-gram fitment + LLM review + optimizer + rerank",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)


# ============================================================
# /api/match: 全流程 (eBay search + label + optimizer)
# ============================================================

class Vehicle(BaseModel):
    year: str
    make: str
    model_guess: str
    vehicle_raw: Optional[str] = ""
    sub_model: Optional[str] = ""  # eBay compat 里叫 Trim; 空串 = "All submodels"


class SourcePartInfo(BaseModel):
    vehicle: Vehicle
    part_description: str
    part_type: Optional[str] = ""
    part_number: Optional[str] = ""


class MatchRequest(BaseModel):
    # deliveryZip / delivery_zip 两种写法都收
    model_config = ConfigDict(populate_by_name=True)

    source_part_info: SourcePartInfo
    use_llm: bool = Field(default=False)
    preset: Optional[str] = Field(default=None)
    # 收货地邮编 (可选): 有效的 5 位美国邮编 → eBay 按这个地址算运费和送达时间;
    # 缺省或写错 → 归一成 None, 静默走原来的通用运费, 不报错。
    delivery_zip: Optional[str] = Field(default=None, alias="deliveryZip")

    @field_validator("delivery_zip", mode="before")
    @classmethod
    def _normalize_zip(cls, value: Any) -> Optional[str]:
        return normalize_delivery_zip(value)
    # eBay 类目路由: 调用方 (/search 选了具体 Part) 查 PCdb -> eBay 映射表得到。
    # 有值 → 第 2 档 compat 用它 (primary 空则依次试 fallback);
    # null → 回退 part_desc_to_category.json 查表 (RO PartLine / 自由文本)。
    ebay_category_id: Optional[int] = Field(default=None)
    ebay_fallback_category_ids: List[int] = Field(default_factory=list)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/match")
def match(request: MatchRequest) -> dict[str, Any]:
    """Run full pipeline: eBay search + label + optimizer."""
    try:
        result = match_source_part(
            request.source_part_info.model_dump(),
            config=PipelineConfig(use_llm=request.use_llm, delivery_zip=request.delivery_zip),
            ebay_category_id=request.ebay_category_id,
            ebay_fallback_category_ids=request.ebay_fallback_category_ids,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc) or exc.__class__.__name__)

    candidates_raw = result.get("candidate_info_list", [])
    preset_name = request.preset or DEFAULT_PRESET

    result["optimizer_result"] = _run_optimizer(candidates_raw, preset_name)
    return result


# ============================================================
# /api/taxonomy: 拉 eBay 分类树 (离线灌库用, 不参与匹配)
# ============================================================

@app.get("/api/taxonomy")
def get_taxonomy(
    root_category_id: int = DEFAULT_ROOT_CATEGORY_ID,
    category_tree_id: str = MOTORS_CATEGORY_TREE_ID,
    resolve_ancestors: bool = True,
) -> dict[str, Any]:
    """
    Fetch eBay Motors taxonomy subtree for the given root category.
    Default 6028 = Parts & Accessories (excludes whole vehicles / tools).

    Motors categories live in category tree 100, not the EBAY_US tree 0.

    Returns a flat list of categories:
    {
      "categories": [
        {"id": 6028, "name": "Parts & Accessories", "parent_id": 6000, "level": 0,
         "is_leaf": false, "full_path": "eBay Motors|Parts & Accessories"},
        {"id": 6030, "name": "Car & Truck Parts & Accessories", "parent_id": 6028, "level": 1, ...},
        ...
      ],
      "total": N,
      "fetched_at": "2026-07-26T..."
    }
    """
    try:
        return fetch_taxonomy(
            root_category_id,
            category_tree_id=category_tree_id,
            resolve_ancestors=resolve_ancestors,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc) or exc.__class__.__name__)


# ============================================================
# /api/rerank: 只跑 optimizer (不碰 eBay)
# ============================================================

class RerankCandidate(BaseModel):
    """接收一条 candidate 的富化数据.

    matcher pipeline 输出的 candidate_info_list[i] 里的字段, 直接传过来.
    matcher_adapter.py 里的 build_candidate_from_matcher 会消费这个 dict.
    """
    item_id: str
    title: Optional[str] = ""
    condition: Optional[str] = ""
    price: Optional[dict] = None                     # {value, currency}
    compatibility: Optional[dict] = None
    candidate_label: Optional[int] = None
    optimizer_fields: Optional[dict] = None          # seller / shipping / warranty / country ...


class RerankRequest(BaseModel):
    candidates: List[RerankCandidate]
    preset: str = Field(default=DEFAULT_PRESET)


@app.post("/api/rerank")
def rerank(request: RerankRequest) -> dict[str, Any]:
    """
    Re-run optimizer on a pre-fetched candidate set with a different preset.

    Not touching eBay — assumes the caller (Next.js) already has the raw
    candidate data (from MatchSearch.rawResponse), just wants a fresh sort.
    """
    if request.preset not in PRESETS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown preset: {request.preset}. Valid: {list(PRESETS)}",
        )

    # 每条 candidate 转成 dict 后交给 optimizer 的 adapter
    candidates_raw = [c.model_dump() for c in request.candidates]
    optimizer_result = _run_optimizer(candidates_raw, request.preset)
    return {"optimizer_result": optimizer_result}


# ============================================================
# 共享: 跑 optimizer 并组装返回体
# ============================================================

def _run_optimizer(candidates_raw: list[dict], preset_name: str) -> dict[str, Any]:
    """Run optimizer over label=1 candidates only. Common code path for both endpoints."""
    eligible_for_optim = [
        (idx, c) for idx, c in enumerate(candidates_raw)
        if c.get("candidate_label") == 1
    ]

    result: dict[str, Any] = {
        "preset_used": preset_name,
        "eligible": [],
        "rejected": [],
        "meta": {
            "total_input_from_matcher": len(candidates_raw),
            "total_label_1": len(eligible_for_optim),
            "total_eligible": 0,
            "total_rejected": 0,
            "min_eligible_price": None,
        },
    }

    if not eligible_for_optim:
        return result

    try:
        optim_cands = [build_candidate_from_matcher(c) for _, c in eligible_for_optim]
        optim_out = optimize(optim_cands, preset=preset_name)

        result["eligible"] = [
            {
                "item_id": e["candidate"].item_id,
                "rank": e["rank"],
                "total": round(e["total"], 2),
                "price_score": round(e["price_score"], 2),
                "speed_score": round(e["speed_score"], 2),
                "quality_score": round(e["quality_score"], 2),
            }
            for e in optim_out["eligible"]
        ]
        result["rejected"] = [
            {
                "item_id": r["candidate"].item_id,
                "reason": r["reason"],
            }
            for r in optim_out["rejected"]
        ]
        result["meta"].update({
            "total_eligible": optim_out["meta"]["total_eligible"],
            "total_rejected": optim_out["meta"]["total_rejected"],
            "min_eligible_price": optim_out["meta"]["min_eligible_price"],
        })
    except Exception as exc:
        result["meta"]["error"] = f"{type(exc).__name__}: {exc}"

    return result