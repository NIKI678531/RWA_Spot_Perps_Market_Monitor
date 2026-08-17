# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A monitor for the tokenized real-world-asset (RWA) market — tokenized stocks / ETFs / funds / commodities
across CEX + DEX spot venues and cross-venue perpetuals. It tracks market scale, trading volume, venue and
issuer competition, and — the core differentiator — **detects demand anomalies** (a product nobody traded
suddenly getting bought).

`ARCHITECTURE.md` at the repo root is the authoritative design document. Read it before making structural
changes. `CONTEXT.md` is the glossary — use its terms, do not invent synonyms.

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

`index.tsx` / `App.tsx` boot the app; `pages/` for the 10 screens, `components/` for UI, `api/` for backend
clients, `styles/` for CSS. Served behind nginx in Docker at `/`, so API calls hit same-origin `/api/...`.

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

## Chart rules

- Never plot two different `MetricScope` values on one Y axis. Use dual axes or split charts. `assert_same_axis()` enforces the stock/flow case.
- Never use pie/stacked charts for overlapping categories — the shape implies additivity.
- All numerals use `{typography.numeric}` (`tnum`) so columns align.
- `Not verified` renders as a grey placeholder, never a zero-height bar.
- Charts use ECharts, themed through `ConfigProvider`-level tokens. Never override antd with `!important` or inline styles.

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
