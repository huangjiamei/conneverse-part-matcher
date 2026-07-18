"""FastAPI HTTP service wrapping the matcher pipeline.

Run with:
    uvicorn end_to_end_part_matcher.service:app --host 0.0.0.0 --port 8001 --reload
"""

from __future__ import annotations

from typing import Any, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .pipeline import PipelineConfig, match_source_part

from algorithms.optimizer import (
    build_candidate_from_matcher,
    optimize,
    PRESETS,
)

DEFAULT_PRESET = "sameDayJob"


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


class SourcePartInfo(BaseModel):
    vehicle: Vehicle
    part_description: str
    part_type: Optional[str] = ""
    part_number: Optional[str] = ""


class MatchRequest(BaseModel):
    source_part_info: SourcePartInfo
    use_llm: bool = Field(default=False)
    preset: Optional[str] = Field(default=None)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/match")
def match(request: MatchRequest) -> dict[str, Any]:
    """Run full pipeline: eBay search + label + optimizer."""
    try:
        result = match_source_part(
            request.source_part_info.model_dump(),
            config=PipelineConfig(use_llm=request.use_llm),
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc) or exc.__class__.__name__)

    candidates_raw = result.get("candidate_info_list", [])
    preset_name = request.preset or DEFAULT_PRESET

    result["optimizer_result"] = _run_optimizer(candidates_raw, preset_name)
    return result


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