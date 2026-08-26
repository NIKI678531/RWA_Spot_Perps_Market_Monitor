# RWA Market Monitoring

The language of tokenized real-world-asset markets as this system models them: what is being traded,
where, by whom, how we decide that demand for something has changed, and what happens to that finding
afterwards.

Several terms here collide with the vocabulary of the upstream data providers. Where they do, this file
is authoritative and the provider's usage is listed under `_Avoid_`.

The last two sections — *How a change becomes a decision* and *How a number is published* — describe the
business layer added in R1. Before it, an alert was the end of the pipeline; now it is the start of a
piece of work that someone owns, resolves and reviews.

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

**Primary theme**:
The one theme an underlying is counted under when shares have to add up to 100%. Every in-scope
underlying has exactly one, and it is the only theme axis a stacked share chart may use.
_Avoid_: Main tag, default theme

**Secondary theme**:
Any further theme an underlying also belongs to. Many-to-many and useful for discovery, but never
summed — stacking secondary themes counts the same volume more than once.
_Avoid_: Sub-theme, extra tag

**Theme version**:
A dated revision of the theme definitions and their mappings, with the reason and the person who
confirmed it. The system proposes a mapping; product research confirms it. Older versions are kept so a
past edition can be replayed with the definitions that were in force at the time.
_Avoid_: Theme config, taxonomy update

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

**Verification status**:
How much of a displayed number we stand behind: `VERIFIED` (observed, complete), `PARTIAL` (observed,
but some contributing sources are missing), `NOT_VERIFIED` (not observed), `STALE` (last successful
observation is older than the metric's freshness threshold), `EMPTY` (observed, and there genuinely was
no activity). Five states, not a boolean — `EMPTY` and `NOT_VERIFIED` differ in what they permit a
reader to conclude.
_Avoid_: Data status, availability, health

**Data age**:
How long ago the observation behind a displayed number was made, counted from `observed_at`. Shown on
every page, because a number without an age cannot be trusted and management will assume it is current.
_Avoid_: Freshness, lag, delay

**Value object**:
The envelope every displayable number travels in: value, unit, metric scope, metric dimension, raw
value, adjusted value, verification status, observed at, window, source count, and — for ratios —
weight basis. A number without its envelope cannot be rendered, because nothing downstream would know
whether it may be added.
_Avoid_: Data point, metric, field

**Weight basis**:
The quantity a ratio was averaged over, stated in the response rather than assumed by the reader —
"share weighted by adjusted spot volume". A ratio aggregate without one is rejected, not defaulted.
_Avoid_: Weighting, denominator (the denominator is one part of it)

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
A detected change in demand, with the evidence that produced it. Not a notification and not the end of
the pipeline: an alert is a piece of work with an owner, a state, a resolution and an audit trail.
_Avoid_: Anomaly, signal, event, notification

**Evidence**:
The inputs to an alert's own decision — observed value, baseline, sample size, market session, rule
name, confirmation count. An alert without complete evidence is not publishable, at any severity.
_Avoid_: Details, context, metadata

**Counter-evidence**:
The facts that argue against the alert being real demand — a large raw/adjusted gap, a single venue
carrying all of it, a thin pool, missing cross-venue confirmation, a cold baseline. Shown next to the
evidence rather than left for the reader to think of, because an alert page that only argues one side
trains people to trust it uncritically.
_Avoid_: Caveats, risks, notes

**Confirmation**:
The number of consecutive qualifying snapshots a time-series finding has now survived. One makes it
tentative; two make it confirmed and publishable to management surfaces.
_Avoid_: Repeats, occurrences (`occurrence_count` is the separate 24h de-duplication counter)

**Publication gate**:
The set of conditions a finding must clear before any user sees it: in-scope `rwa_tier`, a single legal
metric scope, notional at or above the absolute floor, verifiable data, and complete evidence.
Time-series findings must additionally have left cold start and reached two confirmations. Detection
produces candidates; the publication gate decides which of them exist as far as the product is
concerned.
_Avoid_: Filter, threshold, validation

**Awakening**:
The specific pattern of an entity moving from dormant to materially traded. Kept distinct from a spike,
which is growth in demand that already existed.
_Avoid_: Spike, surge, breakout

### How a change becomes a decision

**Decision loop**:
The five steps every finding travels: detect, verify, explain, act, review. The product is organised
around these steps rather than around data sources, and a page that supports none of them does not
belong in the navigation.
_Avoid_: Workflow, process, funnel (a funnel loses people; this one closes)

**Alert state**:
Where an alert sits in its lifecycle: `DETECTED` (system-internal, never rendered), `TENTATIVE`
(published with a stated confidence caveat), `PUBLISHED`, `CLAIMED`, `IN_REVIEW`, `ACTIONED`,
`RESOLVED`, plus the two exits `FALSE_POSITIVE` and `DISMISSED`. State changes are recorded, never
overwritten in place.
_Avoid_: Status (reserved for source and verification status), stage

**Owner**:
The person accountable for resolving one alert or one data gap. Exactly one at a time; reassignment is
an audited event, not a field edit. High and critical alerts are expected to be claimed, or explicitly
declined with a reason, within one working day.
_Avoid_: Assignee, responsible, watcher

**Claim**:
An owner taking an alert out of the unassigned queue. The first human act in the loop and the point
from which handling time is measured.
_Avoid_: Assign, accept, ack

**Business note**:
An owner's written reading of what a finding means commercially. Distinct from evidence, which is what
the system observed — the note is what a person concluded from it.
_Avoid_: Comment, remark

**False positive**:
An alert an owner has judged not to reflect real demand, with the reason recorded. Feeds threshold
review; never silently deleted, because the rate of these is itself a tracked metric.
_Avoid_: Wrong, invalid, noise

**Dismissal**:
A real but not actionable finding, closed with a standard reason. Different from a false positive: the
system was right, the business chose not to act.
_Avoid_: Ignore, reject, close

**Research task**:
A unit of follow-up work created from an alert — investigate an underlying, validate demand with a
channel, prepare an issuance case. The link back to the originating alert is what makes the
signal-to-research conversion rate measurable.
_Avoid_: Ticket, todo, follow-up

**Issuance candidate**:
An underlying or theme that has entered structured evaluation for a possible product. A candidate is a
record with evidence attached, not an approval.
_Avoid_: Product idea, pipeline item

**Evaluation gate**:
One of the five stages a candidate passes: discovery, data confirmation, small-scale validation,
feasibility, product committee. Each has an entry condition, a downgrade condition, an owner and a
named output. No score, quadrant position or heatmap shade may move a candidate through a gate.
_Avoid_: Approval step, checkpoint

**Coverage status**:
Where our own product line stands against an observed demand: `COVERED`, `CANDIDATE`, `GAP`,
`COMPETITOR_FIRST`. The join between market data and our product master, and the reason the market map
can say something about us and not only about the market.
_Avoid_: Product status, availability

**Competitor-first**:
Demand another issuer already serves and we do not. A specific coverage status rather than a comment,
because it is the one that changes what a channel conversation should be about.
_Avoid_: Competition, rival product

**Launch window**:
The 1h, 6h and 24h observation points after a new asset or contract goes live, each stored as its own
immutable snapshot with its own conclusion. Late data produces a revision, never an edit to a window
that has already closed.
_Avoid_: Ramp-up, first day, onboarding

**Retrospective**:
The record closing an alert: final conclusion, whether it was real, what decision it influenced, why it
was a false positive if it was, and any threshold change it suggests. Closing without one is not
permitted.
_Avoid_: Post-mortem, review notes

**Data gap**:
A named, assignable defect in the data — a source failing, a mapping missing, a baseline still cold —
carrying the pages and conclusions it blocks. The queue that data operations works from.
_Avoid_: Bug, issue, error

**Audit record**:
The append-only trail of who changed what, when, and from which state. Covers alert handling, theme
confirmations, threshold edits and edition revisions. What makes a published conclusion defensible
months later.
_Avoid_: Log, history, changelog

### How a number is published

**Edition**:
A named, self-consistent view of the whole product at one point in time. Every page, export and share
link carries one, and switching edition preserves any other filter that remains legal.
_Avoid_: Version, snapshot (a snapshot is one observation; an edition is one whole reading of them)

**Live edition**:
The continuously refreshed edition, updated hourly, showing its snapshot time, data age and next
refresh. What the product research team works in.
_Avoid_: Realtime, current, latest

**Frozen edition**:
The immutable edition published at 09:00 and 17:00 HKT. Once written it is never modified — the point
of it is that two people quoting the morning edition are quoting the same numbers.
_Avoid_: Daily report, snapshot version

**Revision**:
A new numbered version of a frozen edition, issued when late or corrected data arrives, carrying the
reason and the difference from the version it supersedes. The superseded version remains readable.
_Avoid_: Update, fix, correction (the correction is the reason; the revision is the artefact)

**As-of**:
The data cut-off an edition represents, as distinct from the time it was generated. Both are shown;
confusing them makes a report look fresher than its inputs.
_Avoid_: Timestamp, date, generated at

**Share edition**:
A read-only, redacted frozen edition for readers outside the team. Carries the public market evidence,
the scope notes, the as-of and the version number.
_Avoid_: Public link, external report

**Redaction**:
Removing internal fields — candidates, owners, handling actions, business notes, issuance status,
thresholds, unpublished source detail — from a share edition. Performed server-side by omitting the
fields; hiding them in the front end is not redaction.
_Avoid_: Masking, filtering, hiding

### Source access

**Auth mode**:
How a source must be reached: `PUBLIC`, `API_KEY`, or `CHALLENGE` (human-verification gated, e.g.
Cloudflare Turnstile).
_Avoid_: Auth type, access level

**Reference-only source**:
A source we deliberately do not collect from, retained in the registry so its evaluation is not repeated.
_Avoid_: Disabled, inactive, deprecated
