"""FastAPI HTTP service wrapping the matcher pipeline.

Run with:
    uvicorn end_to_end_part_matcher.service:app --host 0.0.0.0 --port 8001 --reload
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .pipeline import PipelineConfig, match_source_part

# Optimizer 接入
from algorithms.optimizer import (
    build_candidate_from_matcher,
    optimize,
)

# Preset 默认硬编码, 前端做完再暴露参数
DEFAULT_PRESET = "sameDayJob"


app = FastAPI(
    title="Conneverse Part Matcher",
    version="0.2.0",
    description="eBay retrieval + MPN labeling + n-gram fitment + optional LLM review + optimizer ranking",
)

# 允许 Next.js dev server 跨域调用。生产环境要收紧到具体域名。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)


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
    use_llm: bool = Field(default=False, description="Enable LLM semantic review for n-gram review cases")
    preset: Optional[str] = Field(default=None, description="Optimizer preset name; defaults to sameDayJob")


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness check."""
    return {"status": "ok"}


@app.post("/api/match")
def match(request: MatchRequest) -> dict[str, Any]:
    """Run the full matcher pipeline + optimizer for one source part."""
    try:
        result = match_source_part(
            request.source_part_info.model_dump(),
            config=PipelineConfig(use_llm=request.use_llm),
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc) or exc.__class__.__name__)

    # ---------- Optimizer 排序 ----------
    # 只把 matcher 判定 label=1 的候选送去 optimizer.
    # label=0 (拒) 和 label=None (待复核) 不进优化, 保留在 candidate_info_list 但没有 optimizer_rank.
    candidates_raw = result.get("candidate_info_list", [])
    preset_name = request.preset or DEFAULT_PRESET

    eligible_for_optim = [
        (idx, c) for idx, c in enumerate(candidates_raw)
        if c.get("candidate_label") == 1
    ]

    optimizer_result: dict[str, Any] = {
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

    if eligible_for_optim:
        try:
            optim_cands = [
                build_candidate_from_matcher(c) for _, c in eligible_for_optim
            ]
            optim_out = optimize(optim_cands, preset=preset_name)

            # 把 optimizer 结果按 item_id 索引, 方便 route 那边合并
            optimizer_result["eligible"] = [
                {
                    "item_id": e["candidate"].item_id,
                    "rank": e["rank"],
                    "total": round(e["total"], 2),
                    "price_score": round(e["price_score"], 2),
                    "quality_score": round(e["quality_score"], 2),
                }
                for e in optim_out["eligible"]
            ]
            optimizer_result["rejected"] = [
                {
                    "item_id": r["candidate"].item_id,
                    "reason": r["reason"],
                }
                for r in optim_out["rejected"]
            ]
            optimizer_result["meta"].update({
                "total_eligible": optim_out["meta"]["total_eligible"],
                "total_rejected": optim_out["meta"]["total_rejected"],
                "min_eligible_price": optim_out["meta"]["min_eligible_price"],
            })
        except Exception as exc:
            # optimizer 挂了不影响 matcher 结果返回
            optimizer_result["meta"]["error"] = f"{type(exc).__name__}: {exc}"

    result["optimizer_result"] = optimizer_result
    return result