# SightLine - AML/Fraud Triage Dashboard (Frontend)

Next.js 16 (App Router) dashboard for bank compliance analysts. Shows a live,
severity-ranked list of transaction risk assessments and a per-transaction
detail view (typology signals, ML component scores, contribution factors,
historical precedent cases, and a manual governance/review-status control).

This is the frontend half of the SightLine contract only. It is built and
validated against the repo-root schemas (`risk_assessment_schema.json`,
`risk_assessment_stream_event_schema.json`) and does not require the FastAPI
backend to be running - see **Mock mode** below.

## Running it

```bash
cd frontend
npm install
npm run dev
```

Then open http://localhost:3000. By default the dashboard runs in **mock
mode** and starts producing synthetic data immediately - no backend needed.

Other scripts:

```bash
npm run build   # production build (also type-checks)
npm run start   # serve the production build
npm run lint    # eslint
```

## Mock mode vs. live mode

Controlled by a single env var, read at build/runtime:

```bash
# frontend/.env.local
NEXT_PUBLIC_DATA_SOURCE=mock   # "mock" (default) | "live"
NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws/risk-stream
```

- **`mock` (default)**: `lib/mockGenerator.ts` fabricates conformant
  `risk_assessment_stream_event_schema.json` events shaped like
  `risk_assessment_example.json`, with varied transaction ids, scores, and
  triggered typologies (drawn from the 13 typologies in
  `lib/typologies.ts`, a local copy of `topfraudandtables.json`'s
  id/name/category/points). On mount it seeds ~16 initial `ASSESSMENT_CREATED`
  events, then keeps emitting a new event every 1.6-4.2s: mostly new
  transactions, some `ASSESSMENT_UPDATED` re-scores of existing ones, and
  some `REVIEW_STATUS_CHANGED` governance transitions - so the table updates
  live without any backend running. This is the default because the real
  model/backend may not exist yet at any given time.
- **`live`**: set `NEXT_PUBLIC_DATA_SOURCE=live` and the dashboard instead
  opens a WebSocket to `NEXT_PUBLIC_WS_URL` (defaults to
  `ws://localhost:8000/ws/risk-stream`), expects the same stream-event
  envelope on every message, and reconnects with backoff on drop. Events are
  deduplicated/ordered using the envelope's monotonically increasing
  `sequence` field (an event is applied only if `sequence` is greater than
  the last one seen).

Both modes feed the exact same state/reducer code
(`lib/useRiskStream.ts`): `ASSESSMENT_CREATED` / `ASSESSMENT_UPDATED` upsert
the full assessment by `transaction_id`; `REVIEW_STATUS_CHANGED` patches only
the `governance` field on the matching row. Switching the env var is the only
change needed to point the same UI at a real backend later.

There is no build-time dependency on anything outside `frontend/` - the
typology metadata and one example payload were used only as reference while
authoring `lib/typologies.ts` and `lib/mockGenerator.ts`; nothing outside
`frontend/` is read or imported at runtime.

## Severity formula (ranking)

The main table is always ranked by a client-computed **severity** score
(computed the same way regardless of whether data came from mock or a live
backend, so ranking behavior never depends on backend availability):

```
severity = 0.65 * risk.overall_risk_score
         + 0.35 * exposure.typology_strength_points
```

Both inputs are already 0-100. The 65/35 weighting favors the ML/RPT score
as the primary signal while still surfacing transactions with strong
deterministic typology exposure even when the ML layer hasn't scored them
yet (`model_version: "rule-engine-only"` per the contract - the rule layer
is always populated, the ML layer may not be). Ties are broken by
`risk_tier` rank (CRITICAL > HIGH > MEDIUM > LOW), then by most recent
`scored_at`. See `lib/severity.ts` for the exact implementation.

The table's "Sort by" control lets an analyst override the default ranking
with Risk tier, Typology strength, or Most recently scored - severity is
always recomputed live as new stream events arrive, regardless of which sort
is selected.

## Filtering

Independent, combinable filters for:
- `risk.risk_tier` (LOW/MEDIUM/HIGH/CRITICAL)
- typology `category` (the 7 categories in `topfraudandtables.json`) -
  matches any assessment with at least one **triggered** typology signal in
  a selected category
- `governance.review_status` (PENDING/IN_REVIEW/ESCALATED/CLEARED/SAR_FILED)

See `lib/filters.ts` / `components/Filters.tsx`.

## Detail view

Clicking a row opens a drawer (`components/DetailDrawer.tsx`, deep-linkable
via `?tx=<transaction_id>`) with, in order:

1. **Typology signals** (`components/TypologySignals.tsx`) - the triggered
   T01-T13 signals with category, points, and narrative, presented first as
   the auditable "why". Not-triggered typologies are available in a
   collapsed disclosure below, not mixed in with the primary explanation.
2. **Component risk scores** (`components/charts/ComponentScoresChart.tsx`)
   - horizontal bar per component (amount/frequency/geography/counterparty/
     pattern/velocity), one shared 0-100 x-axis, sequential-blue magnitude
     ramp. No radar chart, no dual axes.
3. **Top contribution factors**
   (`components/charts/ContributionChart.tsx`) - `explanation.top_column_scores`
   as thin horizontal bars, sorted descending, values as direct labels.
4. **Similar historical precedent cases** (`components/ContextRows.tsx`) -
   `explanation.top_relevant_context_rows` with an `outcome` badge per row.
5. **Governance** (`components/GovernancePanel.tsx`) - a manual control to
   record a `review_status` + analyst name. `requires_human_review` is always
   `true` per the contract; this system never blocks or clears a transaction
   automatically, and the panel's copy is written to read as a manual
   analyst action ("record analyst decision"), never as an automated one.

## Styling / theming

All colors are CSS custom properties defined once in `app/globals.css`
(status palette for `risk_tier`, categorical palette for typology
`category`, sequential blue for magnitude, chart surfaces/ink/gridlines) -
components look up colors by fixed id (`components/TierBadge.tsx`,
`components/CategoryChip.tsx`), never by array index, so filtering out a
category never repaints the others. Risk tier and category are always
paired with an icon/dot + text label, never color alone.

Light/dark mode is driven by `data-theme` on `<html>`, initialized before
hydration by an inline `beforeInteractive` script in `app/layout.tsx` (reads
`localStorage`, falls back to `prefers-color-scheme`), and toggled at runtime
via `lib/useTheme.ts` (`components/ThemeToggle.tsx`).

## Project layout

```
frontend/
  app/                    root layout, globals.css (design tokens), page.tsx
  components/             UI components (table, filters, drawer, charts, badges)
  components/charts/      ComponentScoresChart, ContributionChart
  lib/types.ts             TypeScript mirror of risk_assessment_schema.json /
                           risk_assessment_stream_event_schema.json
  lib/config.ts            NEXT_PUBLIC_DATA_SOURCE / NEXT_PUBLIC_WS_URL
  lib/severity.ts          severity formula + sort comparator
  lib/filters.ts           filter state + matching logic
  lib/typologies.ts        local copy of the 13 typology defs + 7 categories
  lib/mockGenerator.ts     mock stream event generator
  lib/useRiskStream.ts     mock/live orchestration hook (upsert-by-id, reconnect)
  lib/useTheme.ts          data-theme read/write via useSyncExternalStore
```

## Verification performed

`npm run build` and `npm run lint` pass clean. The dev server was started
and loaded in a real (headless) browser in mock mode to confirm: the table
renders and is severity-ranked; new/updated rows arrive live from the mock
generator and the table re-sorts; tier/category/review-status filters narrow
the list correctly; the detail drawer opens with working component-score and
contribution charts and historical-case badges; the governance control
records a review-status change that patches the row in the table; and both
light and dark mode render with correct contrast and no console errors.
