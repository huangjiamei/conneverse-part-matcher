# Fitment Decision Engine

`algorithms.fitment` is the canonical fitment-validation seam for Conneverse.
It evaluates one `FitmentQuestion` against a batch of supplier/catalog
`CandidatePart` rows and returns ranked `FitmentDecision` objects.

The engine owns the final `match` / `review` / `reject` decision. Individual
methods are evidence adapters only:

- `MpnEvidenceAdapter` emits hard support for exact acceptable MPN identity.
- `NgramTextEvidenceAdapter` emits soft support from part/position/vehicle text similarity.
- `FitmentConflictEvidenceAdapter` emits hard contradictions for explicit side,
  position, drive-type, engine, or model-year conflicts.

`confidence` is a fitment gating/ranking score, not a calibrated probability.
The fitment engine does not choose Fastest Delivery or Most Cost-Effective
procurement options; it is a guardrail consumed by later procurement scoring.

## Example

```python
from algorithms.fitment import CandidatePart, FitmentDecisionEngine, FitmentQuestion

question = FitmentQuestion(
    vehicle="2015 Toyota Camry LE 2.5L",
    part_description="front brake pads",
)

candidates = [
    CandidatePart(
        candidate_id="camry-pads",
        title="Ceramic Front Brake Pads for 2012-2017 Toyota Camry 2.5L",
        vehicle_text="2012-2017 Toyota Camry 2.5L",
    ),
]

evaluation = FitmentDecisionEngine().evaluate(question, candidates)
print(evaluation.decisions[0].decision, evaluation.decisions[0].confidence)
```
