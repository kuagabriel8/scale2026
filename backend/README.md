# SightLine Backend

FastAPI backend for the SightLine AML/fraud-triage dashboard (project
TEAM_03). Serves risk assessments that combine:

- **Deterministic layer** (`typology_signals` + `exposure`): computed for
  real by `../build_fraud_features.py` from HANA-sourced data, loaded from
  `../cleaned_data/TRANSACTION_FRAUD_FEATURES.parquet` (150,000 rows) and
  `../cleaned_data/COMPANY_FRAUD_FEATURES.parquet` at startup. Always
  populated, auditable, rule-based - nothing here is a model prediction.
- **ML/RPT layer** (`risk` + `explanation`): mocked (see "Scorer
  abstraction" below) until a real SAP-RPT integration is wired in.

Every response is built to conform to `../risk_assessment_schema.json`
(assessments) and `../risk_assessment_stream_event_schema.json` (WebSocket
events) - both files live at the repo root and are shared with the frontend.

## Running it

```bash
cd backend
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The app loads `../cleaned_data/TRANSACTION_FRAUD_FEATURES.parquet` (and the
supporting tables it joins for narrative text: `TRANSACTIONS.parquet`,
`TRANSACTIONS_BIC_COUNTRY.parquet`, `COUNTRIES.parquet`,
`COMPANY_FRAUD_FEATURES.parquet`, `TRANSACTION_BASELINES.parquet`) once at
startup - takes a few seconds for the full 150k rows. Interactive docs at
`http://localhost:8000/docs`.

Useful env vars (all optional, see `app/config.py`):

| Var | Default | Purpose |
|---|---|---|
| `CORS_ORIGINS` | `http://localhost:3000` | comma-separated allowed origins (REST + WS) |
| `STREAM_EVENTS_PER_SECOND` | `5` | pace of the simulated live feed |
| `STREAM_RESCORE_EVERY_N` | `7` | roughly 1-in-N emitted events is an `ASSESSMENT_UPDATED` re-score instead of a new `ASSESSMENT_CREATED` |
| `STREAM_LOOP` | `true` | loop back to the start of the dataset after exhausting all 150k rows, so a demo can run indefinitely |
| `VALIDATE_RESPONSES` | `true` | dev-mode runtime `jsonschema` assertion on every assessment/event built (disable for a hypothetical prod deployment if the overhead matters) |
| `MAX_ROWS` | unset (all 150,000) | cap rows loaded, for fast local iteration |
| `MODEL_VERSION` | `dummy-mock-v0` | value reported in `model_version` |
| `PORT` / `HOST` | `8000` / `0.0.0.0` | (informational - pass explicitly to uvicorn as above) |

## Endpoints

- `GET /risk-assessments` - paginated, sortable, filterable list.
  Query params: `page`, `page_size` (default 25, max 200), `sort`
  (`severity` default, or `overall_risk_score` / `typology_strength_points` /
  `transaction_id`), `order` (`asc`/`desc`, default `desc`), `risk_tier`
  (`LOW`/`MEDIUM`/`HIGH`/`CRITICAL`), `category` (one of the 7 typology
  categories - matches transactions with >= 1 *triggered* typology signal in
  that category), `review_status`. Response: `{items, total, page, page_size, sort}`
  where each `items[i]` validates against `risk_assessment_schema.json`.
- `GET /risk-assessments/{transaction_id}` - single full assessment. 404 if
  unknown.
- `PATCH /risk-assessments/{transaction_id}/review` - body
  `{"review_status": "...", "reviewed_by": "..."}`. Updates
  `governance.review_status`/`reviewed_by` and broadcasts a
  `REVIEW_STATUS_CHANGED` event (full assessment envelope) to every
  connected `/ws/risk-stream` client. 404 if unknown.
- `WS /ws/risk-stream` - live push channel, envelopes per
  `risk_assessment_stream_event_schema.json`. Supports multiple concurrent
  clients (broadcast). Optionally send `{"type": "get", "transaction_id": N}`
  to pull one assessment on demand (e.g. for reconnect/backfill) - server
  replies with an `ASSESSMENT_UPDATED` envelope.
- `GET /health` - liveness check.
- `GET /docs`, `GET /openapi.json` - auto-generated OpenAPI docs.

CORS is configured for `http://localhost:3000` (REST + WebSocket) via
`CORS_ORIGINS`.

## Severity ranking

There's no `SEVERITY` column in the source data, so the API defines it
explicitly (see `app/scoring_math.py:severity()`):

```
severity = max(risk.overall_risk_score, exposure.typology_strength_points)
```

Both inputs are already on a comparable 0-100 scale. `max` (rather than a
weighted average) means a transaction that's extreme on *either* axis - a
rule-engine typology hit that maxed out the 100-point cap, or an ML score
that's high despite few/no rule hits - surfaces to the top of the triage
queue, instead of a strong signal on one axis being diluted by a weak
signal on the other. This is the default sort (`?sort=severity`, `desc`).

## Scorer abstraction (mocked ML/RPT layer)

`app/scorer.py` defines `BaseScorer` (ABC) with one method:

```python
def score(self, ctx: ScoringContext) -> dict:
    """Returns {"risk": {...}, "explanation": {...}}"""
```

`ScoringContext` carries exactly what the ML layer is allowed to depend on
per `topfraudandtables.json`'s governance clause (grounded in, not
independent of, the deterministic layer): `transaction_id`,
`total_typology_points`, `exposure_factor`, `typology_signals`, and an
`update_count` (incremented on each simulated re-score).

**`DummyScorer`** (active implementation) fabricates `overall_risk_score`
via `15 + TOTAL_TYPOLOGY_POINTS * 0.65 + noise(±12)`, clamped to [0, 100] -
correlated with the rule layer rather than pure random - plus
`model_confidence`, `risk_tier`, `is_anomaly`/`anomaly_type`, per-category
`component_scores`, and an `explanation` (`top_column_scores`,
`top_relevant_context_rows`, and a `narrative_text` grounded strictly in
`typology_signals`/`risk` fields, per governance's `llm_role` constraint).
The "noise" is a deterministic hash of `(transaction_id, update_count)`
(`scoring_math.py`'s sin/mod trick), not `random.random()` - so repeated
`GET`s of the same transaction return identical numbers unless a rescore
bumped `update_count`, and the same math vectorizes over the full 150k-row
dataframe for fast list-endpoint sorting.

**`RPTScorer`** is a stub (`raise NotImplementedError`) with a docstring
pointing at `../rpt_predict_demo.py`'s OAuth client-credentials +
`POST .../predict` pattern (`target_columns`/`explanations` config). To go
live: implement `RPTScorer.score()` following that pattern, then swap
`DummyScorer()` -> `RPTScorer()` in `app/main.py`'s lifespan hook - no other
file needs to change, since every caller depends only on `BaseScorer.score()`.

## Simulated live streaming

There's no real live transaction feed in this environment. `app/streaming.py`'s
`run_stream_simulator()` background task (started in `app/main.py`'s
lifespan hook) walks `TRANSACTION_FRAUD_FEATURES` rows ordered by
`INITIATED_AT` ascending and, at `STREAM_EVENTS_PER_SECOND` (default 5/sec):

- emits `ASSESSMENT_CREATED` for each "newly arrived" transaction, or
- roughly 1 in `STREAM_RESCORE_EVERY_N` (default 7) ticks, instead re-emits
  an already-seen transaction as `ASSESSMENT_UPDATED` (via
  `DataStore.bump_rescore()`, which increments that transaction's
  `update_count` so its mocked ML score/explanation actually changes,
  simulating "a later transaction pushed it into a structuring window").

A process-wide monotonic `sequence` counter (`ConnectionManager`) is shared
across the simulator and the `REVIEW_STATUS_CHANGED` broadcast path from the
`PATCH .../review` endpoint, per the stream schema's ordering/dedup
contract. When `STREAM_LOOP=true` (default), the simulator loops back to
the start of the dataset after exhausting all 150k rows, so a demo can run
indefinitely.

## Data layer / narratives

`app/data_store.py` loads the parquet files once at startup and keeps
sort/filter/pagination vectorized (numpy) over the full dataset; the
expensive nested per-transaction object (13 typology signals with narrative
text, ML explanation, governance state) is only built lazily for the rows
actually being returned (one page, one detail lookup, or the "just
arrived" row in the stream).

`app/narratives.py` fills each triggered typology's `narrative_template`
(from `../topfraudandtables.json`) with real row values wherever the
underlying computed columns make that straightforward (e.g. T09's actual
fuzzy-matched sanctioned entity name + score, T05's real
revenue/employees/incorporation-vs-HQ country, T02's real z-score against
the entity's actual most-recent baseline period, T11's actual triggering
jurisdiction/FATF status) - falling back to a generic string built from the
typology's own published detection parameters where the exact per-row inputs
aren't retained as output columns by `build_fraud_features.py` (e.g. T01's
per-originator rolling count, T03's exact inbound sum, T06/T08's
owner-level detail - noted inline in `narratives.py`).

## Tests

```bash
cd backend
python -m pytest tests/ -v
```

`tests/test_api.py` uses FastAPI's `TestClient` (a `MAX_ROWS=500` cap keeps
it fast) and validates every returned assessment/stream event against the
two repo-root JSON Schemas via `jsonschema` (`app/schema_validation.py`),
covering: list pagination/sorting/filtering, single-detail lookup + 404,
the review-status PATCH (including its schema conformance and the
`REVIEW_STATUS_CHANGED` WebSocket broadcast it triggers), and that
`/ws/risk-stream` delivers at least one valid `ASSESSMENT_CREATED` event to
a connecting client.

`app/schema_validation.py`'s `VALIDATE_RESPONSES` setting also runs the same
`jsonschema` validation at runtime in dev mode (default on) - any endpoint
or the streaming simulator will raise loudly on a contract violation instead
of silently shipping a malformed payload.
