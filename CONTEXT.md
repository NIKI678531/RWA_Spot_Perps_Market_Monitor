# RWA Market Monitoring

The language of tokenized real-world-asset markets as this system models them: what is being traded,
where, by whom, and how we decide that demand for something has changed.

Several terms here collide with the vocabulary of the upstream data providers. Where they do, this file
is authoritative and the provider's usage is listed under `_Avoid_`.

## Language

### The thing being traded

**Underlying**:
The real-world security, commodity or index that a token represents — SPY, TSLA, SpaceX, gold, WTI.
The centre of the data model; every question about customer demand resolves to an underlying.
_Avoid_: Asset, instrument, ticker

**Asset**:
One tokenized wrapper of an underlying, on one chain, from one issuer — `SPYB`, `SPYx`, `SPY-ON`.
Three different assets, one underlying.
_Avoid_: Token, coin, product

**Issuer**:
The organization that creates a tokenized wrapper — Ondo, xStocks, bStocks.
_Avoid_: Provider, sponsor, protocol

**RWA tier**:
How close an asset sits to a real backed claim: `CORE_RWA` (custodied or receipt-backed),
`RWA_ADJACENT` (related but not itself tokenized exposure), `SYNTHETIC` (exposure without custody),
`NON_RWA` (crypto-native). Determines whether the asset enters any statistic at all.
_Avoid_: Category, class, type

**Theme**:
A demand grouping cutting across issuers and venues — Pre-IPO, semiconductors, precious metals, energy,
leveraged ETPs, broad indices. What a product-selection conversation is actually about.
_Avoid_: Sector, tag

**Benchmark**:
A display-only grouping of underlyings that represent the same economic exposure through different
instruments — the SPY ETF and the S&P 500 index. Exists for comparison, never for aggregation.
_Avoid_: Peer group, index

### Where it trades

**Venue**:
A place where an asset trades — a centralized exchange, a DEX, or a perpetual DEX.
_Avoid_: Exchange, market, platform

**Pair**:
One asset trading at one venue. The grain at which spot volume is observed.
_Avoid_: Market, listing

**Pool**:
A DEX liquidity pool. Carries reserves and, uniquely among our sources, separate buy and sell counts.
_Avoid_: LP, AMM

**Perp DEX**:
An independently deployed perpetuals market on Hyperliquid's HIP-3 permissionless infrastructure.
Distinct from the exchange hosting it — one exchange, many perp DEXs.
_Avoid_: Sub-exchange, subaccount

### How it is measured

**Metric scope**:
Which of the five non-additive families a number belongs to: spot market cap, spot volume, DEX liquidity,
perp volume, perp open interest. Two numbers of different scope may sit side by side and may never be
added.
_Avoid_: Metric type, unit, measure

**Metric dimension**:
Whether a metric is a `STOCK` (a level at a point in time), a `FLOW` (an amount over a window), or a
`RATIO`. Stocks and flows may not share a chart axis; ratios may never be summed at all.
_Avoid_: Kind, category

**Raw volume**:
Turnover as the source reported it, including pairs whose quotes the source itself flags as suspect.
_Avoid_: Reported volume, gross volume

**Adjusted volume**:
Turnover after excluding pairs carrying a quality flag. Always presented next to raw volume, never
instead of it — the two can differ by three orders of magnitude.
_Avoid_: Clean volume, real volume, filtered volume

**Quality flag**:
A data provider's marker that a pair's quote is suspicious or stale (CoinGecko's `anomaly` and `stale`).
A statement about data hygiene, not about market behaviour.
_Avoid_: Anomaly — that word is reserved for demand anomalies in this system

**Not verified**:
A metric we failed to observe. Distinct from zero, which is an observed absence of activity.
_Avoid_: Missing, null, N/A, no data

**Snapshot**:
One complete observation of an entity at one timestamp. Snapshots are appended, never revised — a
correction is a new snapshot, not an edit.
_Avoid_: Record, row, update

### How demand change is detected

**Market session**:
The trading state of the *underlying* market at the moment of a snapshot: `RTH`, `PRE`, `AH`,
`CLOSED_WEEKDAY`, `CLOSED_WEEKEND`, `CLOSED_HOLIDAY`. Tokens trade continuously; the securities behind
them do not, so this is the axis every baseline is stratified on.
_Avoid_: Day type, trading day, calendar

**Baseline**:
The robust median and MAD of one entity's history for one metric within one market session. The
reference against which "sudden" is defined.
_Avoid_: Average, norm, historical mean

**Cold start**:
The period during which an entity has fewer than fourteen same-session snapshots. Detectors record but
do not fire — the baseline is not yet something we would defend.
_Avoid_: Warm-up, bootstrap

**Cross-sectional detector**:
A detector that compares an entity against its peer group at the current moment. Needs no history, so it
works on day one.
_Avoid_: Peer detector, relative detector

**Time-series detector**:
A detector that compares an entity against its own past. Requires a baseline that has left cold start.
_Avoid_: Historical detector, trend detector

**Alert**:
A detected change in demand, with the evidence that produced it. What this system exists to emit.
_Avoid_: Anomaly, signal, event, notification

**Evidence**:
The inputs to an alert's own decision — observed value, baseline, sample size, market session, rule name.
An alert without evidence is not publishable.
_Avoid_: Details, context, metadata

**Awakening**:
The specific pattern of an entity moving from dormant to materially traded. Kept distinct from a spike,
which is growth in demand that already existed.
_Avoid_: Spike, surge, breakout

### Source access

**Auth mode**:
How a source must be reached: `PUBLIC`, `API_KEY`, or `CHALLENGE` (human-verification gated, e.g.
Cloudflare Turnstile).
_Avoid_: Auth type, access level

**Reference-only source**:
A source we deliberately do not collect from, retained in the registry so its evaluation is not repeated.
_Avoid_: Disabled, inactive, deprecated
