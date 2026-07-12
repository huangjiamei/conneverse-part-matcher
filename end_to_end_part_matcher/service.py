"""FastAPI HTTP service wrapping the matcher pipeline.

Run with:
    uvicorn end_to_end_part_matcher.service:app --host 0.0.0.0 --port 8001 --reload
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .pipeline import PipelineConfig, match_source_part


app = FastAPI(
    title="Conneverse Part Matcher",
    version="0.1.0",
    description="eBay retrieval + MPN labeling + n-gram fitment + optional LLM review",
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


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness check."""
    return {"status": "ok"}


@app.post("/api/match")
def match(request: MatchRequest) -> dict[str, Any]:
    """Run the full matcher pipeline for one source part."""
    try:
        result = match_source_part(
            request.source_part_info.model_dump(),
            config=PipelineConfig(use_llm=request.use_llm),
        )
        return result
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc) or exc.__class__.__name__)