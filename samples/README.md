# Insight Service – sample payloads

This directory contains end-to-end examples for the Insight Service API.

| File | What it is |
|------|------------|
| `request.json` | A realistic `SessionInput` body suitable for `POST /v1/insights/generate` and `POST /v1/insights/generate-from-raw`. |
| `response.json` | The full `InsightGenerateResponse` returned by the pipeline for `request.json` (analytics + insights + summaries + meta). |

## Try it

Start the service (any of the verification commands work — see the project README), then:

```bash
# Generate insights for the sample session
curl -s -X POST http://localhost:8000/v1/insights/generate \
     -H "Content-Type: application/json" \
     --data @samples/request.json | jq

# Fetch the persisted bundle by session_id
curl -s http://localhost:8000/v1/insights/sample-call-2026-04-22 | jq

# Sub-resources
curl -s http://localhost:8000/v1/insights/sample-call-2026-04-22/summary  | jq
curl -s http://localhost:8000/v1/insights/sample-call-2026-04-22/speakers | jq
curl -s http://localhost:8000/v1/insights/sample-call-2026-04-22/timeline | jq
```

`response.json` is the expected shape; the actual values may shift slightly as the engines evolve, but the keys are stable.
