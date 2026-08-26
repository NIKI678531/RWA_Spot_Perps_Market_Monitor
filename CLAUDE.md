# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A decision radar for the tokenized real-world-asset (RWA) market — tokenized stocks / ETFs / funds /
commodities across CEX + DEX spot venues and cross-venue perpetuals. It tracks market scale, trading
volume, venue and issuer competition, and — the core differentiator — **detects demand anomalies**
(a product nobody traded suddenly getting bought).

R1 (PRD v2.0, 2026-08-26) is a **rebuild of the product surface, not a restyle**. The pipeline, scopes
and detectors are kept; what changes is that the system now has to answer *what happened, is it
credible, what does it mean, who acts next* — and to carry the answer through to a resolution someone
signed. Every finding runs the loop **detect → verify → explain → act → review**. A screen that serves
none of those five steps does not belong in the navigation.

`ARCHITECTURE.md` at the repo root is the authoritative design document. Read it before making
structural changes. `CONTEXT.md` is the glossary — use its terms, do not invent synonyms.
`docs/REQUIREMENTS-R1.md` holds the numbered R1 requirements and their acceptance criteria; cite the
ID (`HOME-002`, `ALERT-004`) in commits and PRs that implement one.

## Stack

- Backend: Python 3.12+, FastAPI, SQLAlchemy 2.0, Alembic, PyMySQL. Managed with `uv` (root `pyproject.toml` + `uv.lock`).
- Frontend: React 18 + TypeScript + Webpack, antd 5, **ECharts**, framer-motion, lucide-react (managed with `npm`; separate `frontend/package.json`).
- DB in compose: MySQL 8.4. Default local fallback (no env): `sqlite:///./app.db`.
- Scheduling: APScheduler. Reporting: openpyxl (xlsx) + python-docx (docx).

## Common commands

Run from the repo root unless noted:

- Backend dev server: `npm run backend` (runs `uv run uvicorn main:app --reload --port 8025` in `backend/`).
- Frontend dev server: `npm run frontend`.
- Backend tests: `npm run backend:test` (pytest; `testpaths = ["backend/tests"]`, `addopts = "-q"`).
  - Single test: `cd backend && uv run --group dev pytest tests/path/to/test_file.py::test_name`.
- Format: `uv run --group dev black .` (check-only: `uv run black --check .`).
- Type check: `uv run --group dev mypy backend` (scans `backend/` only, excludes `frontend/`).
- Alembic migrations:
  - Upgrade: `npm run backend:migrate` (= `cd backend && uv run alembic upgrade head`).
  - Autogenerate revision: `npm run backend:revision -- -m "message"`.
- Full stack via Docker: `docker compose up --build`.
  - Backend: `http://localhost:8025/api`; Frontend (nginx): `http://localhost:8085/`; MySQL: `localhost:3307`.
  - The backend container auto-runs `alembic upgrade head` before starting uvicorn.

## Architecture

### Backend layout (`backend/`)

- `main.py` — thin compatibility entrypoint so `uvicorn main:app` keeps working; re-exports `app` from `app.main`.
- `app/main.py` — `create_app()` assembles the FastAPI instance. Routes mount under `settings.normalized_api_base_path`; docs at `{base}/api/docs`. Prepend the base path when writing clients or tests.
- `app/api/router.py` — aggregates sub-routers under `app/api/routes/`. Add new feature routes here.
- `app/core/config.py` — `Settings` via `pydantic_settings` reading `.env`.
- `app/core/metrics.py` — the metric scope type system. `app/core/sessions.py` — `MarketSession` classification.
- `app/db/` — `session.py` (engine) and `base.py` (declarative base used by Alembic autogenerate).
- `app/models/` — SQLAlchemy ORM. `app/schemas/` — Pydantic request/response models. Keep these layers distinct.
- `app/services/` — the pipeline, in strict layer order:
  - `ingest/` — one collector per source. **Fetch and store raw only. No unit conversion, no dedup, no scope logic here.**
  - `normalize/` — dedup, underlying mapping, `rwa_tier` assignment, quality screening, venue name canonicalization.
  - `analytics/` — rollups, concentration (HHI / Top-N), baselines.
  - `anomaly/` — `engine.py`, `scoring.py`, and one file per detector in `detectors/`.
  - `report/` — `excel.py`, `word.py`.
  - `scheduler.py` — APScheduler job registration.

### Frontend layout (`frontend/src/`)

`index.tsx` / `App.tsx` boot the app; `pages/` for the screens, `components/` for UI, `api/` for backend
clients, `styles/` for CSS. Served behind nginx in Docker at `/`, so API calls hit same-origin `/api/...`.

### Product surface (R1)

Six P0 destinations, grouped in the rail as **decide → research → market structure → govern**:

| Page | Answers | Group |
|:--|:--|:--|
| Decision Home | The four questions below, plus credibility and what to act on | 决策 |
| Anomaly Radar | The alert work queue — claim, evidence, act, resolve, review | 决策 |
| Underlying 360 | Everything about one underlying: wrappers, issuers, venues, perps, alerts, our coverage | 研究 |
| Theme Demand | Which demand themes are heating up, and where our product line has gaps | 研究 |
| Data Quality | Whether a conclusion is publishable at all, and the gap queue behind it | 治理 |
| Reports & Review | Editions, daily digest, alert retrospectives, threshold review | 治理 |

Spot Scale / Venues / Perps are retained as P1 market-structure depth and must drill into Underlying
360. A standalone Issuers page is R2; in R1 issuer competition is embedded in the pages above.

The home page's four questions — these are the page's structure, not a copy suggestion:
Q1 what is trading most, Q2 what is heating up fastest, Q3 how are new products performing, Q4 what is
ready for issuance evaluation. Each card carries answer, change, evidence, counter-evidence, confidence
and one CTA.

**Underlying 360 stays out of the navigation.** It is an entity page reached by drilling down, not a
destination — putting it in the rail forces the user to pick an entity before seeing anything.

### Design system

`DESIGN.md` at the repo root is the authoritative UI spec (CSOP Intelligent Hub — Material You-style
glassmorphism for financial UIs). **Do not edit `DESIGN.md`** — it is shared across projects. Consult it for
token names (`colors.*`, `typography.*`, `rounded.*`, `spacing.*`), component variant naming
(`{name}-{state}`), motion vocabulary (`dur-* / ease-*`), and the eight principles. Use tokens — never
hardcode colors, radii, or spacing.

`docs/DATAVIZ.md` covers charts, which `DESIGN.md` does not. Its palettes are derived from existing
`DESIGN.md` tokens and it must not introduce new colour values. On any conflict, `DESIGN.md` wins.
`docs/UI-LAYOUT.md` defines the four page templates and the navigation shell.

## Domain rules (non-negotiable — these encode real financial-reporting constraints)

These are not style preferences. Violating them produces numbers that are wrong in a way that looks right.

1. **Metric scopes are never additive across each other.** `SPOT_MARKET_CAP`, `SPOT_VOLUME`, `DEX_LIQUIDITY`, `PERP_VOLUME`, `PERP_OI` are five distinct kinds of number. Summing across them is meaningless. Aggregation goes through `safe_sum()`, which raises `MetricScopeViolation`. Never bypass it.
2. **`RATIO`-dimension metrics are never summed at all.** Turnover, buy/sell ratio, market share, spread, slippage and funding cannot be added even within one scope. Use `weighted_avg()` and state the weighting basis explicitly.
3. **Overlapping CoinGecko categories are not additive either.** Tokenized Stock / Tokenized ETF / Ondo / xStocks / bStocks overlap by construction. Only the deduplicated union row is a valid total. Rows carry `is_additive`; respect it in both API and charts.
4. **`Not verified` ≠ `0`.** A failed or rate-limited fetch is a missing observation, not a zero. Write `NOT_VERIFIED` to `fetch_log`; never coerce to 0. UI renders a grey placeholder, never a zero bar.
5. **Raw and quality-adjusted volume are reported side by side.** Anomaly/stale-flagged pairs are excluded from adjusted, kept in raw. Never show only one. (Reference case: Native (BSC) reports ~$29.3mn raw but ~$216 adjusted — 17 of 19 pairs flagged.)
6. **Baselines are stratified by `market_session`, not by calendar day.** RWA tokens trade 24/7 but underlyings do not. `RTH / PRE / AH / CLOSED_WEEKDAY / CLOSED_WEEKEND / CLOSED_HOLIDAY` differ structurally. Baselines key on `(entity, metric, market_session)` or Monday mornings produce mass false alarms.
7. **Use median + MAD, not mean + stdev.** Volume distributions are extremely right-skewed (top-10 contracts = 78.2% of Binance TradFi volume). Means are dominated by spikes.
8. **Alerts must be explainable.** Every alert writes `alert_evidence` with the raw value, baseline, sample size, market session, and rule name. An alert you cannot justify to management is noise.
9. **Preserve source labels verbatim.** e.g. Binance classifies some ETFs/leveraged ETPs as `EQUITY`. Store `source_underlying_type` as-is *and* our `analysis_group` alongside. Never overwrite an exchange's own label.
10. **Absolute-magnitude floor on alerts.** No alert below ~$50k notional. $500 → $5,000 is +900% and commercially meaningless.
11. **`rwa_tier` gates every statistic.** `CORE_RWA` / `RWA_ADJACENT` / `SYNTHETIC` are in scope; `NON_RWA` (crypto-native) is not. `NON_RWA` rows exist only as `dim_benchmark` reference and must never enter a ranking, rollup or alert. `dim_benchmark` is a display-only join — it never sums.
12. **The two detector families stay separate.** Cross-sectional detectors (`X*`) compare an entity against its peer group *now* and need no history. Time-series detectors (`T*`) compare an entity against its own past and require ≥14 same-session snapshots. Never let a `T*` detector fire on a cold baseline, and never route an `X*` finding through baseline gating.
13. **Generated Excel stays plain.** No conditional formatting, no embedded charts, no merged cells — the workbook must remain copy-pasteable and machine-readable for downstream analysis. Visual polish belongs in the web UI only.
14. **Detection is not publication.** A detector emitting a finding does not make it visible. Everything crosses the publication gate first: in-scope tier, single legal scope, ≥ ~$50k notional, verifiable data, complete evidence — and for `T*`, cold start exited plus two consecutive confirmations. Management-facing surfaces (home page, email digest) additionally take `high`/`critical` only. Never render a raw detector output.
15. **Frozen editions are immutable.** The 09:00 and 17:00 HKT editions are written once. Late or corrected data produces a numbered revision carrying its reason and diff; the superseded version stays readable. Never `UPDATE` an edition row, and never let a "fix" silently change a number someone already quoted.
16. **An alert is a business object, not a notification.** It has one owner, a state (`DETECTED → TENTATIVE/PUBLISHED → CLAIMED → IN_REVIEW → ACTIONED → RESOLVED`, exiting via `FALSE_POSITIVE` or `DISMISSED`), and an append-only audit record for every transition, note and reassignment. Closing requires a retrospective or a standard declined reason. Data repairs never rewrite a historical alert — they append an evidence version or raise a new alert.
17. **No composite score approves anything.** Heatmap shades, quadrant positions and severity scores rank and prioritise; they never gate. An issuance candidate moves between evaluation gates only through a recorded human decision, and the UI must not imply otherwise (no "auto-approve", no total-score column across heatmap rows).
18. **Counter-evidence ships with evidence.** Any published alert or candidate shows what argues against it — raw/adjusted gap, single-venue concentration, thin pool, no cross-venue confirmation, cold baseline. One-sided alert pages train people to stop reading them.
19. **Only `primary_theme` is exclusive.** Shares that add to 100% and stacked theme charts use `primary_theme` only. Secondary themes are many-to-many and must never be stacked or summed. Theme definitions and mappings are versioned with an author and a reason, so a past edition replays under the definitions in force then.
20. **Every displayable number travels in the value object.** `value, unit, metric_scope, metric_dimension, raw_value, adjusted_value, verification_status, observed_at, window, source_count, weight_basis`. Ratio aggregates without `weight_basis` are rejected by the backend, not defaulted. Front-end chart components assert the same invariants — chart data can also come from local derivation.
21. **Five verification states, and they mean different things.** `VERIFIED` / `PARTIAL` / `NOT_VERIFIED` / `STALE` / `EMPTY`. `EMPTY` is an observed zero and may be stated as one; `NOT_VERIFIED` may not be rendered as a number at all; `STALE` blocks new management-level conclusions. Collapsing any of these into the others is rule 4 all over again.
22. **Share editions are redacted server-side.** Candidates, owners, actions, business notes, issuance status, thresholds and unpublished source detail are omitted from the response for a share viewer. Hiding them in the front end is not redaction. A permission denial must not reveal whether the record exists.
23. **View state lives in the URL.** Scope, edition, as-of, time window, filters, sort and raw/adjusted are URL state, and every export inherits exactly them plus the timezone and data cut-off. A screenshot someone pastes into a deck has to be reproducible six weeks later.

## Chart rules

- Never plot two different `MetricScope` values on one Y axis. Use dual axes or split charts. `assert_same_axis()` enforces the stock/flow case.
- Never use pie/stacked charts for overlapping categories — the shape implies additivity. Stacked theme shares are `primary_theme` only.
- All numerals use `{typography.numeric}` (`tnum`) so columns align.
- `Not verified` renders as a grey placeholder, never a zero-height bar. `Empty` is a real zero and renders as one, labelled.
- Charts use ECharts, themed through `ConfigProvider`-level tokens. Never override antd with `!important` or inline styles.
- One treemap per scope. The volume treemap and the OI treemap sit side by side and are never combined, totalled, or given a shared legend implying a sum.
- Default to Top 8 + Other, expandable to Top 20. `Other` may only aggregate items of the same scope that are additive.
- Tooltips carry the full metric name, scope, dimension, raw and adjusted, unit, observed time and verification status — not just a number.
- Heatmap columns are independently normalised states. Never sum a row into a readiness or approval score.
- No free-form metric picker. Users choose from prepared views; letting them drop any two metrics on one chart is how cross-scope and stock/flow mixing gets back in.
- Every chart ships a "view data table" entry point, an `aria` description, keyboard focus and non-colour encoding. Clicking an underlying anywhere lands on Underlying 360 with the filter context preserved.

## Hard constraints (from AGENTS.md)

- **No PVCs.** Backend deployment must not depend on any PersistentVolumeClaim. Production K8s does not provide one.
- Generated reports and any file/media must go to cloud object storage (TOS) or be persisted in the database. Do not keep files inside the backend container.

## Conventions

- Add new HTTP endpoints as a module under `app/api/routes/` and include the router in `app/api/router.py`.
- Keep `backend/main.py` as-is (compat shim); put real wiring in `app/main.py`.
- Black line length 88; target `py312`. Mypy runs against `backend/` only.
- One detector per file in `services/anomaly/detectors/`, registered in `engine.py`. Filenames carry the family prefix (`x1_*.py`, `t2_*.py`).
- Fact tables are append-only. Never `UPDATE` a `fact_*` row — write a new snapshot.
- New data sources are declared in `source_registry` with an `auth_mode` and a `status`. Sources marked `REFERENCE_ONLY` are never scheduled.
- The publication gate lives in `services/anomaly/publication.py` and is the only path from a detector output to a stored `alert`. Detectors never decide visibility themselves.
- Business-object code lives in `services/workflow/` (alert lifecycle, research tasks, issuance candidates, coverage, data gaps, audit) and edition code in `services/editions/` (freeze, revision, share redaction). Neither is a pipeline stage: they read analytics output and never write `fact_*`.
- Every state-changing endpoint writes an audit record in the same transaction as the change. If the audit write is optional, the change is not auditable.
- Displayable numbers cross the API boundary through the shared value-object schema, not as bare floats.
