# End-to-end single part matcher

This folder contains a Python single-record version of the current dataset-building
flow:

1. input: one `source_part_info` object, matching rows in
   `data/test_dataset/new_dataset_and_match_algorithm/test_dataset_v4.json`;
2. retrieval: eBay MPN search -> compatibility search -> keyword fallback;
3. MPN labeling: exact, CAPA suffix-tolerant, and title-token checks;
4. n-gram filtering: run existing `algorithms.fitment` on non-positive / unlabeled candidates, including candidates with a different manufacturer part number;
5. LLM judgment: only n-gram `review` candidates are sent to the semantic judge.

A different MPN is not treated as a hard fitment rejection: OEM and aftermarket
manufacturers may use different numbers for compatible parts. The semantic judge
uses the same strict vehicle/part rules as `scripts/relabel_candidates_by_semantics.py`
and never receives candidate MPN fields.

## Minimal input shape

```json
{
  "vehicle": {
    "year": "2019",
    "make": "Subaru",
    "model_guess": "Impreza",
    "vehicle_raw": "2019 SUBA Impreza w/Continuously Variable Transmission"
  },
  "part_description": "Lower grille",
  "part_type": "oem",
  "part_number": "57731FL30A"
}
```

You can also pass a full dataset row; the code will read `row["source_part_info"]`.

## Usage

```bash
python -m end_to_end_part_matcher.pipeline \
  --input data/test_dataset/new_dataset_and_match_algorithm/test_dataset_v4.json \
  --line-no 1 \
  --no-llm \
  --pretty
```

For production LLM review, set `OPENAI_API_KEY` in `.env` or the environment and
omit `--no-llm`. eBay retrieval requires `EBAY_CLIENT_ID` and `EBAY_CLIENT_SECRET`.
Transient eBay/OpenAI failures are retried automatically; use `--request-delay`
only when explicit pacing between eBay detail requests is required.

Programmatic use:

```python
from end_to_end_part_matcher import match_source_part

record = match_source_part(source_part_info)
```
