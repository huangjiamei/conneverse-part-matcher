# Part Matcher Web UI

The local web UI accepts one `source_part_info` record and displays the result
from the existing eBay -> MPN -> n-gram -> optional LLM pipeline. It is an
experimental view of the current single-part flow, not the production
Procurement Safety Floor or the complete RO-level recommendation workflow.

## Run

Configure eBay credentials in the repository `.env` first:

```text
EBAY_CLIENT_ID=...
EBAY_CLIENT_SECRET=...
```

Add `OPENAI_API_KEY` only if you plan to enable LLM semantic review in the UI.
Then start the server:

```bash
python -m end_to_end_part_matcher.web --open
```

The default address is <http://127.0.0.1:8000/>. Use `--host` and `--port` to
change it. This is a local development interface and is not hardened for public
internet deployment.
