"""Hard fitment-conflict evidence adapter."""

from __future__ import annotations

import re
from typing import List, Set, Tuple

from algorithms.fitment.adapters.ngram_text import normalize_text
from algorithms.fitment.models import CandidatePart, FitmentEngineConfig, FitmentEvidence, FitmentQuestion


class FitmentConflictEvidenceAdapter:
    """Finds explicit mutually-exclusive fitment conflicts."""

    method = "fitment_conflict"

    def __init__(self, config: FitmentEngineConfig | None = None) -> None:
        self.config = config or FitmentEngineConfig()

    def evaluate(self, question: FitmentQuestion, candidate: CandidatePart) -> Tuple[FitmentEvidence, ...]:
        request_part_description = normalize_text(
            question.part_description,
            max_year_range_expansion=self.config.max_year_range_expansion,
        )
        request_position = normalize_text(
            question.position,
            max_year_range_expansion=self.config.max_year_range_expansion,
        )
        request_vehicle = normalize_text(
            question.vehicle,
            max_year_range_expansion=self.config.max_year_range_expansion,
        )
        candidate_title = normalize_text(
            candidate.title,
            max_year_range_expansion=self.config.max_year_range_expansion,
        )
        candidate_vehicle_text = normalize_text(
            candidate.vehicle_text,
            max_year_range_expansion=self.config.max_year_range_expansion,
        )
        candidate_fitment_notes = normalize_text(
            candidate.fitment_notes,
            max_year_range_expansion=self.config.max_year_range_expansion,
        )

        conflicts = self._hard_fitment_conflicts(
            request_text=" ".join(x for x in (request_part_description, request_position, request_vehicle) if x),
            candidate_text=" ".join(x for x in (candidate_title, candidate_vehicle_text, candidate_fitment_notes) if x),
        )
        if not conflicts:
            return ()

        return (
            FitmentEvidence(
                method=self.method,
                effect="hard_contradiction",
                confidence=1.0,
                reasons=conflicts,
                metrics={"hard_conflicts": conflicts},
            ),
        )

    def _hard_fitment_conflicts(self, request_text: str, candidate_text: str) -> Tuple[str, ...]:
        request_tokens = set(request_text.split())
        candidate_tokens = set(candidate_text.split())

        conflicts: List[str] = []
        exclusive_groups = (
            frozenset({"left", "right"}),
            frozenset({"front", "rear"}),
            frozenset({"upper", "lower"}),
            frozenset({"2wd", "rwd", "4wd", "awd"}),
            frozenset({"driver", "passenger"}),
        )

        for group in exclusive_groups:
            request_hits = request_tokens & group
            candidate_hits = candidate_tokens & group
            if request_hits and candidate_hits and request_hits.isdisjoint(candidate_hits):
                conflicts.append(
                    "exclusive token conflict: "
                    f"request={sorted(request_hits)} candidate={sorted(candidate_hits)}"
                )

        request_engines = self._engine_tokens(request_text)
        candidate_engines = self._engine_tokens(candidate_text)
        if request_engines and candidate_engines and request_engines.isdisjoint(candidate_engines):
            conflicts.append(
                "engine displacement conflict: "
                f"request={sorted(request_engines)} candidate={sorted(candidate_engines)}"
            )

        request_years = self._year_tokens(request_text)
        candidate_years = self._year_tokens(candidate_text)
        if request_years and candidate_years and request_years.isdisjoint(candidate_years):
            conflicts.append(
                "model year conflict: "
                f"request={sorted(request_years)} candidate={sorted(candidate_years)}"
            )

        return tuple(conflicts)

    @staticmethod
    def _engine_tokens(text: str) -> Set[str]:
        return set(re.findall(r"\b\d(?:\.\d)?l\b", text))

    @staticmethod
    def _year_tokens(text: str) -> Set[str]:
        return set(re.findall(r"\b(?:19|20)\d{2}\b", text))
