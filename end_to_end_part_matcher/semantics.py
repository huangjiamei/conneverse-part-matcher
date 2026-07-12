from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Mapping

from algorithms.fitment import CandidatePart, FitmentDecisionEngine, FitmentEngineConfig, FitmentQuestion

from .utils import load_dotenv, source_vehicle_text

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"


def compatibility_vehicle_text(compatibility: Mapping[str, Any]) -> str:
    return " ".join(str(compatibility.get(k) or "") for k in ("Year", "Make", "Model", "Compatible Makes")).strip()


def compatibility_fitment_notes(compatibility: Mapping[str, Any]) -> str:
    keys = ("Placement on Vehicle", "Placement", "Fitment Type", "Compatibility", "Universal Fitment", "categoryPath")
    return " ".join(str(compatibility.get(k) or "") for k in keys).strip()


def run_ngram_decision(source: Mapping[str, Any], candidate: Mapping[str, Any], *, engine: FitmentDecisionEngine) -> Any:
    compatibility = candidate.get("compatibility") or {}
    question = FitmentQuestion(
        vehicle=source_vehicle_text(source),
        part_description=str(source.get("part_description") or ""),
        acceptable_mpn_set=(),  # MPN was already decided before this semantic/text stage.
    )
    candidate_part = CandidatePart(
        candidate_id=str(candidate.get("item_id") or candidate.get("title") or "candidate"),
        title=str(candidate.get("title") or ""),
        fitment_notes=compatibility_fitment_notes(compatibility),
        vehicle_text=compatibility_vehicle_text(compatibility),
        metadata={"subtitle": candidate.get("subtitle") or "", "condition": candidate.get("condition") or ""},
    )
    return engine.evaluate_one(question, candidate_part)


SEMANTIC_SYSTEM_PROMPT = """You are a strict auto-parts fitment data annotator.

Task:
For each anchor requested part and each candidate listing, decide whether the
candidate should be upgraded to candidate_label=1.

Review scope:
- Review every candidate whose current candidate_label is 0, null, or missing.
- Skip only candidates whose current candidate_label is already exactly 1.

Upgrade to 1 only when the candidate listing is a real replacement for the
anchor requested part:
- same vehicle semantics
- same requested component
- same install position/side when stated
- same key option/feature when stated
- no title/compatibility conflict that would affect installation or use

Critical rules:
1. Same requested component:
   bumper cover must be bumper cover, not reinforcement, grille, bracket,
   absorber, retainer, clip, valance, molding, etc.
   Broad/generic words are not enough. For example, "side molding" is not
   automatically the same as door window molding, belt molding, rocker molding,
   sill molding, bumper molding, or roof molding. Upgrade only if the same
   concrete subcomponent is clear from the anchor and candidate.
   Broad anchors such as support, bracket, molding, retainer, clip, cover,
   panel, tape, protector, emblem, rivet, bolt, nut, latch, extension are
   especially risky: do NOT upgrade a candidate merely because it shares that
   generic noun. If the candidate title adds a concrete subsystem not present
   in the anchor (bumper, radiator, mirror, intercooler, fender, grille, door,
   roof, trunk, wheelhouse, etc.), treat that as unresolved specificity and
   omit it unless the anchor also states the same concrete subsystem.
2. Same installation position:
   front != rear; rear != front; left/driver != right/passenger.
   If the anchor does not state a side/front/rear position, do NOT upgrade a
   candidate that states a narrower position.
3. Same option/feature when stated:
   with fog != without fog; LED != halogen; camera/sensor holes/sunroof/chrome/
   painted/textured differences matter when they are a part-version difference.
4. Vehicle semantics must not conflict:
   Candidate must not conflict with any explicit vehicle semantics in the anchor.
   Treat explicit make, model, series, platform/chassis code, generation,
   submodel, performance variant, trim, body style, powertrain, drivetrain,
   wheelbase, cab/bed configuration, and market-specific version tokens in the
   candidate title as important fitment evidence.

   Do not infer interchangeability across related vehicles, shared platforms,
   sibling models, performance variants, trims, body styles, or powertrains.
   A candidate that names a different or narrower vehicle variant should be
   omitted unless it clearly includes the anchor vehicle semantics too.

   If the candidate lists a different model/platform/variant and only a generic
   brand-level or broad compatibility field appears to overlap, do NOT upgrade.
5. Do NOT use manufacturer part numbers as proof. They are not provided.
   Decide only from vehicle, requested-part text, title, subtitle, compatibility,
   condition.
6. If unsure, be conservative and do NOT upgrade.
7. Only output an upgrade when you are highly confident (roughly 0.90+). If a
   web search or external catalog lookup would be needed to disambiguate, omit
   the candidate instead of guessing.

Return only valid JSON. No markdown."""

SEMANTIC_USER_PROMPT = """Review this single dataset candidate.

Return exactly this JSON shape:
{
  "upgrade_to_label_1": false,
  "confidence": 0.0,
  "reason": "short reason based on vehicle + part semantics only"
}

Candidate data:
{candidate_json}
"""


def parse_json_response(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        starts = [p for p in (text.find("{"), text.find("[")) if p != -1]
        if not starts:
            raise
        obj, _ = json.JSONDecoder().raw_decode(text[min(starts):])
    if not isinstance(obj, dict):
        raise ValueError("LLM response must be a JSON object")
    return obj


class OpenAISemanticJudge:
    def __init__(self, *, model: str, min_positive_confidence: float = 0.85, max_retries: int = 4) -> None:
        load_dotenv()
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self.model = model
        self.min_positive_confidence = min_positive_confidence
        self.max_retries = max(1, max_retries)

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def judge(self, source: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        candidate_json = json.dumps({
            "source_part_info": {
                "vehicle": source.get("vehicle") or {},
                "part_description": source.get("part_description"),
                "part_type": source.get("part_type"),
            },
            "candidate": {
                "current_candidate_label": candidate.get("candidate_label"),
                "title": candidate.get("title") or "",
                "subtitle": candidate.get("subtitle") or "",
                "compatibility": candidate.get("compatibility") or {},
                "condition": candidate.get("condition") or "",
            },
        }, ensure_ascii=False, indent=2)
        prompt = SEMANTIC_USER_PROMPT.replace("{candidate_json}", candidate_json)
        payload = {
            "model": self.model,
            "input": [
                {"role": "system", "content": SEMANTIC_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "text": {"format": {"type": "json_object"}},
            "temperature": 0,
        }
        body = self._request(payload)
        text = body.get("output_text") or ""
        if not text:
            text = "".join(
                content.get("text", "")
                for item in body.get("output", [])
                for content in item.get("content", [])
                if content.get("type") in {"output_text", "text"}
            )
        parsed = parse_json_response(text)
        model_upgrade = parsed.get("upgrade_to_label_1")
        if not isinstance(model_upgrade, bool):
            raise ValueError("LLM response upgrade_to_label_1 must be a boolean")
        confidence = float(parsed.get("confidence") or 0.0)
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("LLM response confidence must be between 0 and 1")
        return {
            "upgrade_to_label_1": model_upgrade and confidence >= self.min_positive_confidence,
            "model_upgrade_to_label_1": model_upgrade,
            "confidence": confidence,
            "reason": str(parsed.get("reason") or "").strip(),
        }

    def _request(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            req = urllib.request.Request(
                OPENAI_RESPONSES_URL,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=180) as response:
                    return json.loads(response.read().decode("utf-8"))
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
                last_error = exc
                if attempt == self.max_retries:
                    break
                time.sleep(2.0 * attempt)
        raise RuntimeError(f"OpenAI request failed after {self.max_retries} attempts: {last_error}")


def apply_ngram_and_llm(
    source: Mapping[str, Any],
    candidates: list[dict[str, Any]],
    *,
    use_llm: bool,
    judge: OpenAISemanticJudge | None = None,
    judge_factory: Callable[[], OpenAISemanticJudge] | None = None,
) -> dict[str, int]:
    engine = FitmentDecisionEngine(config=FitmentEngineConfig())
    counts = {"ngram_match": 0, "ngram_reject": 0, "ngram_review": 0, "llm_match": 0, "llm_reject": 0, "llm_error": 0}
    for candidate in candidates:
        if candidate.get("candidate_label") == 1:
            continue
        decision = run_ngram_decision(source, candidate, engine=engine)
        candidate["ngram_fitment"] = {
            "decision": decision.decision,
            "confidence": decision.confidence,
            "reasons": list(decision.reasons),
            "metrics": decision.metrics,
        }
        if decision.decision == "match":
            _replace_label(candidate, 1, "NGRAM_FITMENT_MATCH")
            counts["ngram_match"] += 1
        elif decision.decision == "reject":
            _replace_label(candidate, 0, "NGRAM_FITMENT_REJECT")
            counts["ngram_reject"] += 1
        else:
            counts["ngram_review"] += 1
            candidate["needs_llm_review"] = True
            if use_llm and judge is None and judge_factory is not None:
                judge = judge_factory()
            if not (use_llm and judge and judge.available):
                candidate["llm_semantic_judgement"] = {"status": "skipped", "reason": "LLM disabled or OPENAI_API_KEY missing"}
                continue
            try:
                llm = judge.judge(source, candidate)
                candidate["llm_semantic_judgement"] = llm
                if llm["upgrade_to_label_1"]:
                    _replace_label(candidate, 1, "LLM_SEMANTIC_MATCH")
                    counts["llm_match"] += 1
                else:
                    _replace_label(candidate, 0, "LLM_SEMANTIC_REJECT")
                    counts["llm_reject"] += 1
            except Exception as exc:
                candidate["llm_semantic_judgement"] = {"status": "error", "error": str(exc)}
                counts["llm_error"] += 1
    return counts


def _replace_label(candidate: dict[str, Any], label: int, source: str) -> None:
    candidate["candidate_label_previous"] = candidate.get("candidate_label")
    candidate["candidate_label_source_previous"] = candidate.get("candidate_label_source")
    candidate["candidate_label"] = label
    candidate["candidate_label_source"] = source
