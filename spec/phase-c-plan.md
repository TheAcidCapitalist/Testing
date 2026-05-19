# Phase C Plan — Data Pipeline

Synthesized from `CLAUDE.md`, `ROADMAP.md`, `spec/universe.md`, `spec/scoring.md`,
and the existing scaffolds in `src/scanner/data/` and `src/scanner/cli.py`.

---

## 1. Scope

### 1.1 What this phase delivers

- `data/storage.py` — complete two-layer DuckDB persistence: per-indicator rows
  `(ticker, exchange, date, indicator_name)` as the source of truth, plus derived
  combo/ranking rows by combination name. Replaces the current incomplete scaffold.
- `data/eodhd.py` — live EODHD client using per-ticker EOD calls for both
  historical backfill and daily refresh (confirmed available on free tier — Probe 2).
  Enforces the 20-call/day hard cap via a daily counter persisted in `tbl_run_log`.
  Designed so upgrading to a paid tier is a configuration change (raise `daily_limit`,
  enable the bulk-EOD endpoint) rather than a rewrite. No per-request sleep is needed;
  the actual throughput limit (1,200/minute) is not a binding constraint at 20 calls/day.
- `data/universe.py` — universe loader with the `sample` scope fully operational
  for the test phase; `us` and `global` scopes guarded behind a paid-tier check.
- `cli.py` — `scanner run-daily --universe sample|us|global` with a full
  orchestration pipeline: load universe → fetch OHLCV → store raw prices → run all
  registered indicators → store per-indicator outputs → store combo + ranking.
- End-to-end gate: `uv run scanner run-daily --universe sample` completes on the
  narrow universe, writes both DuckDB layers, respects the 20-call/day daily cap, and
  is idempotent on re-run.

### 1.2 What this phase does not deliver

- Report generation (Excel, email, dashboard JSON) — Phase D.
- LLM briefing layer — Phase D.
- GitHub Actions workflows and alerting — Phase D.
- Combination selection CLI flag beyond `--universe` — Phase D.
- Backtest harness — Phase E.
- Production data sourcing decision — open; must be resolved before Phase D begins
  but is not a Phase C gate.

---

## 2. Hard operational constraints (test phase)

Two rate-limit mechanisms exist but only one is the binding constraint:

| Limit | Value | Window | Mechanism | Consequence of violation |
|-------|-------|--------|-----------|--------------------------|
| Daily call quota | **20 / calendar day** | Per calendar day | Billing layer; not in API headers | Run aborts; remaining tickers deferred to next day |
| Throughput cap | 1,200 / minute | Rolling 60-second window | `x-ratelimit-limit` header | Practically unreachable at 20 calls/day |

**The 20/day quota is the only binding constraint.** The 1,200/minute throughput cap
(confirmed in Probe 2) is not a practical limit at 20 calls/day — we would need to
fire all 20 calls within a single second to approach it. No per-request sleep is
needed; the client need not enforce any timing delay between calls.

The daily counter is the single enforcement mechanism. Every component that makes
API calls threads a shared `CallBudget` object. All API calls are serialised through
the single client. The counter cannot be bypassed by parallelism.

**Note on `x-ratelimit-remaining`:** This header reflects the per-minute throughput
bucket, not the daily quota. It cannot be used to track daily remaining calls. The
daily counter is maintained in `tbl_run_log.api_calls_used` (see §6.1).

---

## 3. Test phase — locked design

### 3.1 Universe shape: curated list of ~15 liquid global names

Three shapes were considered:

**Single exchange via bulk-EOD.** One call returns all tickers for the day, which is
efficient for the daily refresh. But the bulk endpoint covers only the last trading
day — historical backfill still requires per-ticker calls. A filtered US exchange has
3,000–4,000 qualifying tickers; backfilling them at 20 calls/day takes months.
The bulk endpoint is worth using for the daily-refresh leg, but it does not make a
large universe tractable under the ceiling.

**Index constituents (S&P 500, FTSE 100).** Principled and well-defined. S&P 500
is ~500 tickers; at 20 backfill calls/day that takes 25 days before a single
indicator run. Not viable. A top-20-by-market-cap subset of an index collapses back
to a curated list.

**Curated list of ~15 liquid global names.** The only shape that fits the ceiling
cleanly.

- Historical backfill (one-time, Day 1): 15 per-ticker calls, one per ticker.
  Completes in well under a minute (no pacing needed; 1,200/min throughput cap is not
  a constraint).
- Daily refresh (steady-state): 1 per-ticker call per ticker = 15 calls/day. Budget
  headroom: 20 − 15 = 5 calls/day for overhead, retries, and metadata.
- Expanding by 5 tickers later costs 5 backfill calls; steady-state rises to 20
  calls/day exactly. Any further expansion requires a paid-tier upgrade or the
  universe must shrink to stay within budget.

**Composition:** 15 names drawn from at least 4 regions and 5 sectors so the
regional/sector taxonomy is exercised from the start. All must be highly liquid
names with several years of price history (the `min_history_bars=250` filter must
pass with room to spare). The exact tickers are open decision #2 (see §7).
International coverage depends on what the EODHD free tier actually provides for
non-US exchanges — the probe session (§3.4) must confirm this before tickers are
hardcoded.

### 3.2 Budget arithmetic

| Activity | Calls | Timing |
|----------|-------|--------|
| API probes (one-time; already done) | 3 | Done in Probe sessions 1 and 2 |
| Historical backfill, 15 tickers (one-time) | 15 | Day 1; completes in < 1 minute |
| Daily: per-ticker EOD refresh, 15 tickers | 15 | Every run day |
| Daily: overhead (symbol-list, retries, metadata) | ≤ 5 | Every run day |
| **Daily steady-state total** | **≤ 20** | Tight fit; no headroom for expansion beyond 15 tickers |
| **Day-1 total** | **15** | Backfill = refresh on Day 1; probes already consumed |

**Steady-state budget is tight.** At 15 tickers the daily refresh consumes 15 of the
20 available calls. The 5-call overhead cushion is adequate for retries and occasional
metadata lookups but leaves no room for universe expansion without a paid-tier
upgrade. Any scope larger than ~15 tickers requires Option A (paid EODHD) where the
daily quota is orders of magnitude higher.

**On paid tier:** The bulk-EOD endpoint (blocked on free tier, available on paid)
replaces 15 per-ticker calls with 1 call per exchange. Daily steady-state drops from
15 calls to 1–2, and the universe can expand to thousands of tickers. The client is
designed for this transition to be a configuration change only.

### 3.3 Staging structure

The existing scopes from `universe.md` are retained with sharpened definitions:

| Scope | Definition | Status in Phase C |
|-------|-----------|-------------------|
| `sample` | Hardcoded list of ~15 curated tickers; budget-aware; the primary Phase C target | Fully implemented |
| `us` | US listings passing liquidity filters | **Gated** — `PaidTierRequired` until production sourcing is decided |
| `global` | All exchanges passing liquidity filters | **Gated** — same |

`us` and `global` raise `PaidTierRequired("scope 'us' requires a paid data tier; "
"see spec/phase-c-plan.md §4")` with a pointer to this document. They do not
silently fall back to `sample`.

The `sample` scope label is retained for CLI compatibility. Its spirit shifts from
"fixture-adjacent sanity check" (universe.md) to "the complete free-tier test
universe." These are compatible: the same narrow list serves both purposes.

### 3.4 EODHD probe sessions — complete

Both probe sessions have been completed. Full findings are in
`spec/eodhd-probe-notes.md`. Key results relevant to the client build:

**Probe 1 (2026-05-16) — bulk-EOD endpoint:**
- `GET /eod-bulk-last-day/{exchange}` → HTTP 423 (Locked). Bulk endpoint is
  blocked on the free tier. This closes open decision #3.

**Probe 2 (2026-05-18) — per-ticker EOD, symbol list, rate-limit window:**

1. `GET /api/eod/AAPL.US?period=d&from=...` → HTTP 200. Per-ticker endpoint is
   available. Response shape (7 fields per bar, JSON array, ascending date):
   ```json
   {"date": "2026-05-18", "open": 300.24, "high": 300.66, "low": 294.91,
    "close": 297.84, "adjusted_close": 297.84, "volume": 34313641}
   ```
   **Critical:** the adjusted close field is `adjusted_close`, not `adj_close`.
   The client must rename it to `adj_close` on ingest.

2. `GET /api/exchange-symbol-list/US?type=common_stock` → HTTP 200. Returns 18,462
   rows. Fields per row: `Code`, `Name`, `Country`, `Exchange`, `Currency`, `Type`,
   `Isin`. **No `MarketCapitalization`, `Sector`, or average volume.** This closes
   open decision #5. See open decision #14 for the resulting metadata strategy choice.

3. Rate-limit window: the `x-ratelimit-limit: 1200` header is a per-minute (60-second
   rolling) throughput cap, not the daily quota. The 20/day limit is a separate
   billing-layer cap not reflected in any header. See §2 for the updated constraint
   model. This closes open decision #7 (rate-limit mechanism).

**Non-US exchange coverage:** Not probed. Open decision #4 remains open. The narrow
15-ticker test-phase universe should initially use US-only tickers to avoid this
uncertainty. Non-US coverage can be probed in a future session when needed.

---

## 4. Production sourcing — three positions

The production sourcing decision is deferred. The test phase is fully executable
regardless of which path is chosen. The decision must be made before Phase D begins
because it determines whether `us` and `global` scopes are ever built, and whether
the client layer requires a rewrite. All three positions are treated as first-class
candidates.

### 4.1 Option A: Paid EODHD (single vendor upgrade)

**What changes:** Upgrade to EODHD "All-World Extended" (~$20–50/mo). The free-tier
`EODHDClient` becomes the production client with the ceiling constant raised. No new
abstractions.

**Coverage:** ~70 exchanges, ~60–80k raw tickers, ~8–15k after liquidity filtering.
Genuine global coverage including Asia, EM, and Australia.

**Reliability:** Commercial SLA. Established vendor with published uptime history.
The bulk-EOD endpoint returns all tickers for an exchange in one call — ~70 calls
covers the entire global universe per day, well within a 100k-class daily allowance.

**Terms of service:** Commercial use explicitly permitted. Single data-feed
attribution. No unusual downstream terms.

**Data quality:** Good. Split/dividend-adjusted prices. Sector and region metadata.
Some coverage gaps in frontier markets (expected and acceptable).

**Architecture implications:** Minimal. The test-phase client is already Option A in
all respects except the ceiling and API key. Transition is:
- Remove the `PaidTierRequired` guard in `universe.py`.
- Set a paid API key in `.env`.
- Raise `daily_limit` in `CallBudget` from 20 to the plan allowance.
- Run `--universe us` to backfill.

One session at most. No new components, no new abstractions, no schema changes. The
`source` column in `tbl_prices` stays nullable (single source, no attribution
needed).

**Complexity above test-phase baseline:** ~0.5 sessions.

**Risks:** Single-vendor dependency. If EODHD raises prices or has a multi-day
outage, there is no fallback. Price has been stable historically. Outage risk is
mitigated by the stored indicator outputs: a one-day gap in OHLCV means one day's
indicators are missing but all prior signals remain intact.

---

### 4.2 Option B: Multi-source free stack

**What changes:** Replace or supplement the EODHD client with a mix of unofficial
and free-tier sources. A plausible combination:

- **yfinance** (Yahoo Finance Python wrapper): broad US coverage, some
  international. Unofficial library wrapping an undocumented API.
- **Stooq**: CSV bulk downloads for some European markets. Unofficial.
- **Alpha Vantage free tier**: 25 calls/day, US and some international. Too limited
  for universe coverage but potentially usable as a targeted fallback.

Other sources may be substituted; the list above is illustrative.

**Coverage:** Good for US. Patchy for Europe, poor for EM and frontier markets. No
single source covers 70 exchanges. Coverage is the union; reliability is the
intersection.

**Reliability:** Low. Yahoo Finance has broken yfinance multiple times without
notice. Stooq has gone offline for days. No source has a published SLA. The daily
pipeline will fail on random mornings as sources update their formats or throttle
aggressively. A financial reporting pipeline that depends on unofficial scraping
requires constant maintenance.

**Terms of service:** Yahoo Finance's terms of service prohibit automated data
extraction for commercial or financial product purposes. This is a legal risk that
Options A and C do not carry. Stooq has no published terms. The legal exposure
should be evaluated before choosing this path.

**Data quality:** Variable. Corporate action adjustments differ between sources.
Stale prices are common for non-US tickers. Conflicts between sources on the same
(ticker, date) require a resolution rule, and there is no ground truth to validate
against.

**Architecture implications:** Substantial. Requires:

- An abstract `DataClient` protocol that each source adapter implements, normalising
  different response schemas to the canonical OHLCV frame.
- A `RoutingClient` that selects the primary source per ticker/exchange, falls back
  to secondary on failure, detects conflicts, and records source attribution.
- Per-source rate limiters (each source has different, unofficial, and changeable
  throttling behaviour).
- Source attribution: `tbl_prices` needs a `source` column; downstream reporting
  should surface which prices came from which source.
- Conflict detection and resolution: when two sources return different close prices
  for the same (ticker, date), the system must apply a documented rule (e.g.,
  primary wins; flag if discrepancy exceeds 1%). The rule must be an explicit
  decision before building.
- Ongoing maintenance when any source changes its format.

**Complexity above test-phase baseline:** High. The client layer alone is estimated
at 3–4x the Option A transition work. Ongoing maintenance burden is also
substantially higher.

**Risks:** The highest-risk option. Legal exposure (Yahoo TOS). Brittle pipelines
that fail silently when a source returns stale or malformed data. No alert when a
source goes down without raising an exception. Data quality incidents propagate
directly to indicator outputs and daily signals.

---

### 4.3 Option C: EODHD free tier permanently

**What changes:** Nothing. The 20/day and 2/min ceilings are accepted as permanent.
The production universe is the same ~15-ticker narrow list as the test phase. Scope
never expands.

**Coverage:** 15 tickers. The scanner becomes a personal watchlist tool rather than
a global equity scanner.

**Reliability:** Same as the test phase.

**Terms of service:** Covered by the EODHD free tier. No cost.

**Data quality:** Same as Option A (same vendor, same feed).

**Architecture implications:** Zero. Phase C is the production build.

**Complexity:** None beyond Phase C.

**Risks:** The scanner never achieves its stated purpose. The Phase E backtest is
calibrated on 15 tickers — the resulting weights and parameters will not generalise.
The dashboard and report are a watchlist. This is a valid outcome if the goal shifts
from a broad global scanner to a personal trading workflow tool; it should be a
deliberate choice, not a default.

---

### 4.4 Other vendors worth evaluating

These were not listed in the prompt but surface naturally from the constraints:

**Twelve Data** — free tier gives 800 API credits/day (one credit per OHLCV
endpoint call). This is 40× more than EODHD free. Paid plans start at ~$29/mo.
Supports 50+ exchanges. At the free tier, a universe of ~700–800 tickers is
reachable, making a `us` scope feasible without paying. Coverage and data quality
should be spot-checked against EODHD on the same tickers before committing.

**Tiingo** — ~$10/mo for expanded access. US-focused with some international.
Data quality is good. Not suitable for a 70-exchange global run but viable for a
US-plus-major-markets universe at lower cost than EODHD paid.

Both are worth a one-session evaluation before committing to Option A or B, if cost
is a primary constraint.

---

### 4.5 Trade-off summary

| Dimension | A: Paid EODHD | B: Multi-source free | C: Free forever |
|-----------|--------------|----------------------|-----------------|
| Monthly cost | $20–50 | $0 | $0 |
| Legal clarity | Clear | Risky (Yahoo TOS) | Clear |
| Global coverage | Full (70 exchanges) | US well; international patchy | 15 tickers |
| Reliability | Commercial SLA | Low (unofficial) | Same as test phase |
| Data quality | Good | Variable; requires validation | Good |
| Build complexity (above baseline) | Low (~0.5 sessions) | High (3–4× client layer) | Zero |
| Ongoing maintenance | Low | High | Low |
| Achieves stated purpose | Yes | Partially | No |
| Transition from test phase | Config change | Client layer rewrite | None needed |

---

## 5. Component breakdown

### 5.1 `data/storage.py`

**Purpose:** DuckDB persistence. Two-layer scheme as required by `spec/scoring.md`:
per-indicator rows as the source of truth, derived combo/ranking rows as a
convenience layer recomputeable from the first. Also stores raw OHLCV and universe
metadata.

**Existing scaffold assessment:** The current scaffold must be substantially
rewritten. `tbl_scan_results` stores only `combo_score` and `tier` — predates the
per-indicator storage requirement. There is no `tbl_indicator_outputs`, no
`tbl_combo_results`, no `tbl_run_log`. The existing `tbl_universe` and `tbl_prices`
tables are adequate starting points; `tbl_prices` needs a nullable `source` column
for the multi-source branch.

**Schema sketch:**

```sql
-- Universe metadata (existing; add sector, region columns)
CREATE TABLE IF NOT EXISTS tbl_universe (
    ticker          VARCHAR,
    exchange        VARCHAR,
    name            VARCHAR,
    currency        VARCHAR,
    market_cap_usd  DOUBLE,
    sector          VARCHAR,
    region          VARCHAR,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ticker, exchange)
);

-- Raw OHLCV (existing; add source column)
CREATE TABLE IF NOT EXISTS tbl_prices (
    ticker      VARCHAR,
    exchange    VARCHAR,
    date        DATE,
    open        DOUBLE,
    high        DOUBLE,
    low         DOUBLE,
    close       DOUBLE,
    adj_close   DOUBLE,
    volume      BIGINT,
    source      VARCHAR,   -- 'eodhd' | 'yfinance' | ... ; nullable for single-source branches
    PRIMARY KEY (ticker, exchange, date)
);

-- Layer 1: per-indicator outputs — source of truth for subset re-combination (new)
CREATE TABLE IF NOT EXISTS tbl_indicator_outputs (
    ticker            VARCHAR,
    exchange          VARCHAR,
    date              DATE,
    indicator_name    VARCHAR,
    raw_value         JSON,    -- full compute() dict; flexible across indicator shapes
    normalized_value  DOUBLE,  -- Stage 1 output (0.0–1.0); null for mav_diff_z (no combo role)
    direction         VARCHAR, -- 'buy' | 'sell' | 'neutral' | null (confirmation indicators)
    PRIMARY KEY (ticker, exchange, date, indicator_name)
);

-- Layer 2: derived combo + ranking (new; recomputeable from tbl_indicator_outputs)
CREATE TABLE IF NOT EXISTS tbl_combo_results (
    ticker                VARCHAR,
    exchange              VARCHAR,
    date                  DATE,
    combination_name      VARCHAR,
    direction             VARCHAR,
    combo_score           DOUBLE,
    rank_score            DOUBLE,
    agreement_count       INTEGER,
    n_trade_indicators    INTEGER,  -- denominator for agreement_count / N
    signals_firing        JSON,
    vol_confirmation      VARCHAR,
    volume_confirmation   VARCHAR,
    days_since_breakout   INTEGER,
    PRIMARY KEY (ticker, exchange, date, combination_name)
);

-- Run log: tracks budget usage and enables idempotent re-run (new)
CREATE TABLE IF NOT EXISTS tbl_run_log (
    run_id         VARCHAR,
    run_date       DATE,
    scope          VARCHAR,
    status         VARCHAR,   -- 'started' | 'completed' | 'failed' | 'partial'
    tickers_done   JSON,      -- list of [ticker, exchange] pairs completed this run
    api_calls_used INTEGER,
    started_at     TIMESTAMP,
    finished_at    TIMESTAMP,
    PRIMARY KEY (run_id)
);
```

**Key read/write interface:**

```python
Storage.write_prices(ticker, exchange, df)           -> None   # upsert tbl_prices
Storage.read_prices(ticker, exchange) -> pd.DataFrame          # chronological asc
Storage.write_indicator_outputs(rows: list[dict])    -> None   # upsert tbl_indicator_outputs
Storage.read_indicator_outputs(ticker, exchange, date) -> dict[str, dict]
Storage.write_combo_results(df)                      -> None   # upsert tbl_combo_results
Storage.log_run_start(run_id, date, scope)           -> None
Storage.log_run_ticker_done(run_id, ticker, exchange)-> None
Storage.log_run_end(run_id, status, api_calls)       -> None
Storage.get_completed_tickers(run_id) -> set[tuple[str, str]] # for idempotent resume
```

**Key design decisions:**

- `raw_value` is stored as JSON (the full `compute()` dict). This avoids a
  wide-table design with schema changes for every indicator shape change. The
  `normalized_value` scalar is extracted as a top-level column so combo re-scoring
  is a SQL aggregation, not JSON parsing.
- `tbl_scan_results` is removed entirely. The two new tables replace it.
- `source` on `tbl_prices` is nullable: single-source branches (Options A and C)
  leave it populated as a constant `'eodhd'`; Option B populates it per row from
  the routing client. No schema migration is required between branches.
- All writes are `INSERT OR REPLACE` (upsert on primary key). Re-runs are safe.

**Open questions:**

- Should `raw_value` store the full `compute()` dict or only the fields relevant to
  scoring? Full dict is more useful for the Phase E backtest (the whole series is
  available in storage); the tradeoff is storage size.
- Scale check: 8k tickers × 11 indicators × 252 trading days ≈ 22M rows/year in
  `tbl_indicator_outputs`. DuckDB handles this without issue; disk budget should be
  estimated before Phase D launch.

---

### 5.2 API client layer

**Purpose:** Fetch OHLCV data, normalise it to the canonical DataFrame shape, and
enforce both rate limits. Track call usage against the shared daily budget.

**Canonical DataFrame shape (immutable contract — every source must produce this):**

```python
pd.DataFrame, chronologically ascending, with columns:
  date       datetime64[ns]   # trading date, timezone-naive
  open       float64
  high       float64
  low        float64
  close      float64          # unadjusted close; stored in tbl_prices as-is
  adj_close  float64          # split/dividend-adjusted; passed to compute() as 'close'
  volume     float64
```

RangeIndex. Source-specific metadata (exchange, sector, currency) is passed
separately, not embedded in the OHLCV frame.

**Test phase and Options A and C — single EODHD client:**

**Per-ticker EOD endpoint** (confirmed available on free tier — Probe 2):

```
GET /api/eod/{TICKER}.{EXCHANGE}?api_token=...&period=d&from=YYYY-MM-DD&fmt=json
```

Returns a JSON array of OHLCV bars for one ticker from `from` to today. Used for
both historical backfill (long `from` date on first run) and daily refresh (yesterday
as `from` date in steady state). The parser maps the response to the canonical
DataFrame shape, renaming `adjusted_close` → `adj_close` (see field-name convention
below).

**Bulk-EOD endpoint** (blocked on free tier; available on paid tier):

```
GET /api/eod-bulk-last-day/{exchange}?api_token=...&fmt=json
```

Blocked (HTTP 423) on the free tier (Probe 1). On paid tier, returns all tickers'
last-trading-day OHLCV for an exchange in one call. The client must be structured so
enabling this is a configuration flag (`use_bulk_eod: bool = False`) rather than a
separate code path. When `use_bulk_eod=True`, the orchestrator calls this once per
exchange before the per-ticker loop and reads today's bar from the in-memory result
rather than making per-ticker calls for the daily bar.

**Field-name convention — `adjusted_close` → `adj_close`:**

The EODHD API returns `adjusted_close`. The canonical DataFrame and DuckDB column use
`adj_close`. The client renames this field on ingest. Nothing downstream changes.

**Daily budget — `CallBudget`:**

```python
class CallBudget:
    """Shared daily call counter. Source of truth: tbl_run_log.api_calls_used."""
    def __init__(self, daily_limit: int = 20) -> None: ...
    def charge(self, n: int = 1) -> None: ...   # raises DailyBudgetExceeded if over
    def remaining(self) -> int: ...
```

`daily_limit` is a parameter (default 20 for the free tier). Upgrading to a paid tier
means raising this value and setting `use_bulk_eod=True` — no other changes.

**No per-request sleep is needed.** The throughput cap (1,200/minute, confirmed in
Probe 2) is not a binding constraint at 20 calls/day. Removing the 30-second sleep
also makes the test suite faster: mocked call sequences no longer need sleeps.

**Budget persistence:** `tbl_run_log.api_calls_used` is the canonical counter, updated
by `Storage.log_run_ticker_done()` and `Storage.log_run_end()`. On a same-day re-run,
the orchestrator reads `api_calls_used` from the existing run-log row and initialises
`CallBudget` from that value — the counter does not reset.

**Option B — multi-source additions:**

Option B requires an abstract protocol:

```python
class DataClient(Protocol):
    name: str                             # 'eodhd' | 'yfinance' | 'stooq' | ...
    def fetch_history(
        self, ticker: str, exchange: str, from_date: date, to_date: date
    ) -> pd.DataFrame: ...                # canonical shape
    def fetch_bulk_today(self, exchange: str) -> dict[str, pd.Series]: ...
```

Each source implements the protocol. A `RoutingClient` wraps multiple implementations:
- Maintains a routing table: which ticker/exchange pairs are sourced from which
  provider (primary and fallback).
- On primary failure (timeout or 5xx), falls back to secondary.
- On conflict (both sources return data for the same bar but prices differ by more
  than a configurable threshold), applies the conflict resolution rule (open decision
  #12), logs the conflict to `tbl_run_log`, and stores the primary value.
- Tracks per-source call budgets separately. The unified `CallBudget` counts all
  calls across all sources.
- Records `source` on every write to `tbl_prices`.

**Open questions:**

- Does the EODHD free tier cover non-US exchange history (LSE, TSE, ASX)? Not yet
  probed. Until confirmed, the narrow test-phase universe uses US-only tickers.
  (Open decision #4.)
- For Option B: which specific sources form the stack, and what is the conflict
  resolution rule? Both must be decided before any Option B code is written.
  (Open decision #12.)

---

### 5.3 `data/universe.py`

**Purpose:** Build and cache the filtered list of tickers to scan for the active
scope. Apply the liquidity filters from `spec/universe.md`. Return a DataFrame with
columns `[ticker, exchange, name, currency, market_cap_usd, sector, region]`.

**Existing scaffold assessment:** Substantially incomplete. The scaffold filters
by market cap only (using $500M, not the spec's $750M default), missing
`min_avg_daily_value`, `min_price`, and `min_history_bars`. The `us` and `global`
stubs are absent. The caching strategy deletes and rewrites the whole universe
table on every call rather than using the cached value, and has no scope concept.

**Interface:**

```python
def load_universe(
    scope: Literal["sample", "us", "global"],
    client: DataClient,
    storage: Storage,
    budget: CallBudget,
    *,
    refresh: bool = False,
    min_market_cap_usd: float = 750_000_000,
    min_avg_daily_value: float = 5_000_000,
    min_price: float = 1.0,
    min_history_bars: int = 250,
) -> pd.DataFrame:
```

All filter thresholds are parameters with spec defaults. The function returns the
filtered universe; it does not compute indicators or fetch history.

**Per-scope behaviour:**

`sample`: Returns the hardcoded list of ~15 curated tickers. Makes no API calls
(zero budget consumed for universe loading). The list is a module-level constant in
`universe.py`. The history check (`min_history_bars`) is deferred to the
orchestrator, which validates row count after fetching OHLCV.

`us`: Raises `PaidTierRequired` until the production sourcing decision is made and
a paid key is configured. After the gate is removed: calls `exchange_symbols()` for
US exchanges (NASDAQ, NYSE, NYSE MKT), applies all liquidity filters, caches in
`tbl_universe`. The ADV filter requires either a fundamentals endpoint (one call per
ticker — expensive) or post-fetch computation from 20 bars of price history. See
open decision #6.

`global`: Same guard as `us`. After: iterates all ~70 EODHD exchanges.

**Staging structure survives a paid-tier transition:** Only the guard and the ticker
source differ between scopes. The filtering logic, caching logic, and return schema
are identical. Removing the `PaidTierRequired` guard and providing a paid key is
the entire transition. No new code paths.

**Open questions:**

- Exact ADV computation method: fundamentals endpoint (1 extra call/ticker, not
  feasible on free tier) vs. compute from stored price data (close × volume rolling
  mean over 20 bars — viable once prices are in storage) vs. relax ADV filter to
  market-cap-only for the initial build. (Open decision #6.)
- Sector and region metadata: the EODHD symbol-list endpoint does not return these
  fields (Probe 2). Source is open decision #14 (metadata strategy).
- The exact 15 tickers for `sample`. (Open decision #2.)

---

### 5.4 Orchestrator and CLI

**Purpose:** Daily-run entry point. Orchestrates the full pipeline, enforces both
rate limits throughout, and is safe to re-run after partial failure.

**CLI surface (Phase C):**

```
scanner run-daily --universe sample|us|global
```

`--universe` is the only flag in this phase. Combination selection and all other
flag surface are Phase D's concern.

**Pipeline:**

```
1. Initialise context
   Load or create CallBudget for today (from disk if exists, else fresh).
   Instantiate Storage and DataClient.
   Determine run_id (e.g. ISO date + scope).

2. Load universe
   load_universe(scope=args.universe, ...).
   Result: DataFrame of (ticker, exchange) pairs.

3. Per-ticker loop
   For each (ticker, exchange) in universe:
     a. Skip if ticker in storage.get_completed_tickers(run_id). (idempotent)
     b. Determine fetch range and fetch OHLCV.
        last_stored = storage.most_recent_price_date(ticker, exchange)
        if last_stored >= today: skip (already current, 0 calls).
        else: client.fetch_history(ticker, exchange,
                                   from_date = last_stored + 1 day (or default),
                                   to_date   = today)
        budget.charge(1). Raises DailyBudgetExceeded → go to step 4.
        storage.write_prices(ticker, exchange, df).
        Note: this single call handles both historical backfill (day 1, long from_date)
        and daily refresh (steady state, from_date = yesterday).
     c. Validate OHLCV (§6.7 sanity checks).
     d. Warmup check: if row count < min_history_bars, log skip, continue.
     e. Build compute df: read from storage, set adj_close as 'close'.
     f. Run all registered indicators:
          for name, mod in REGISTRY.items():
              result = mod.compute(df)
     g. Normalise each result to 0–1 (Stage 1 of spec/scoring.md).
     h. storage.write_indicator_outputs(rows).
     i. Compute combo score and ranking for each combination in the registry.
        (scoring.py is called here, inline per ticker.)
     j. storage.write_combo_results(df).
     k. storage.log_run_ticker_done(run_id, ticker, exchange).

4. Finalise
   storage.log_run_end(run_id, status='completed'|'partial', api_calls).
   Print summary: N tickers processed, N signals, N API calls used, remaining budget.
```

**Budget enforcement:** `DailyBudgetExceeded` is caught at the top of the ticker
loop (step 3b). On catch: log remaining tickers, call `log_run_end(status='partial')`,
and exit with code 0. A partial run is not a failure — it is the expected outcome when
the daily budget is exhausted before all tickers are processed. GitHub Actions should
not mark a partial run as a workflow failure.

**Paid-tier upgrade path:** When `use_bulk_eod=True`, step 3 gains a pre-loop
exchange call that fetches today's bar for all tickers in one API call. Steps 3b then
read the daily bar from the in-memory dict without consuming additional budget. The
orchestrator is written with this flag from the start; enabling it on a paid tier
requires only the configuration change — no code path addition.

**Combination registry:** For Phase C, a Python-level constant list of the three
seeded combinations from `spec/scoring.md` (`default`, `breakout_family`,
`mean_reversion`). The orchestrator iterates this list for step 3i. Phase D may
make this configurable.

**`scoring.py` integration:** The orchestrator calls `scoring.py` inline per ticker
in step 3i. `scoring.py` receives the per-indicator outputs for one ticker (from the
dict built in steps 3f–3g) and a combination definition, and returns the combo score
and ranking fields. It does not read from or write to storage — that is the
orchestrator's job.

**Open questions:**

- Where does the `scoring.py` normalisation logic (Stage 1) live — inside
  `scoring.py` or inline in the orchestrator? Recommended: inside `scoring.py` as
  `normalize(indicator_name, raw_result) -> float`. This keeps the scoring logic in
  one place and makes the orchestrator a thin pipeline.
- Total run time for `--universe sample`: with no per-request sleep, the bottleneck
  is indicator computation and DuckDB writes, not API pacing. Estimated 1–2 minutes
  for 15 tickers. Should be measured in session 5 and logged.

---

## 6. Cross-cutting concerns

### 6.1 API call budget management

`CallBudget` is the single authority on daily consumption. Rules:

1. Instantiated once per run by the orchestrator before any API call. Initial value
   read from `tbl_run_log.api_calls_used` for today's run_id (0 if no row yet).
2. On same-day re-run, the existing run log row is loaded and `CallBudget` is
   initialised from `api_calls_used` — the counter does not reset. There is no
   separate JSON budget file; `tbl_run_log` is the canonical store.
3. Every API call in every component goes through `budget.charge()`. The budget
   object is passed as a dependency (not global state) so tests can inject a mock.
4. `DailyBudgetExceeded` causes a clean partial exit. It is not a crash.
5. No per-request sleep is needed. The 1,200/minute throughput cap (confirmed in
   Probe 2) is not a practical constraint at 20 calls/day. The client makes calls
   as fast as the network and storage allow.

### 6.2 Historical backfill

**Test phase (15-ticker narrow universe):**

Day 1 fetches the full history for each of the 15 tickers: 15 calls, well under
1 minute (no pacing needed). Subsequent days add only bars since the last stored date
via the same per-ticker endpoint with a short `from` range. There is no ongoing
backfill problem at the narrow scope.

**Extending the narrow universe incrementally:**

Each additional ticker costs 1 backfill call. The 5-call overhead budget
(20 − 15 steady-state = 5) absorbs up to 5 new tickers per day, at which point
steady-state reaches 20 calls/day exactly. Any further expansion requires either
reducing the existing universe or upgrading to a paid tier.

**Production backfill (Options A and B — decided before Phase D):**

- **Option A:** EODHD paid tier provides a bulk historical endpoint (all tickers for
  an exchange, multi-year range, one call). Full global backfill is estimated at
  1–2 sessions. Strategy: fetch 2 years of history per exchange, filter to the
  passing universe, store, and verify.
- **Option B:** yfinance supports batched multi-ticker downloads. Coverage and
  quality must be validated before treating this as a reliable backfill source.
  Expect 2–4 sessions including validation.

Production backfill strategy is a Phase D pre-launch deliverable, not a Phase C
gate.

### 6.3 Timezone and exchange-close handling

**Target run time:** GitHub Actions cron at approximately 04:00 ET (09:00 UTC).
This clears all prior-session closes for all major regions:

| Region | Typical close (UTC) | Published by 04:00 ET? |
|--------|---------------------|------------------------|
| Asia (Tokyo, Sydney) | 06:00–08:00 UTC prior day | Yes |
| Europe (London, Frankfurt) | 16:00–17:30 UTC | Yes |
| Americas (NYSE, NASDAQ) | 21:00–22:00 UTC | Yes |

EODHD's publish lag (time between exchange close and data availability on the API)
must be confirmed during the probe session. A test call at 04:00 UTC on a
post-trading day verifies that the prior-day bars are present. If the lag is longer
than expected, the cron time shifts right.

**Stale data detection:** After fetching a ticker's bars, compare the most recent
date in the response to the expected prior trading day. If the most recent date is
more than one trading day stale (accounting for weekends and holidays), skip
indicator computation for that ticker and flag it in the run log. Never compute
indicators on a bar that may not be the most recent close.

**Holiday handling:** EODHD does not return data for exchange holidays. A missing
date in the price series is normal. Rolling-window indicators handle sparse series
natively. No special logic is required beyond ensuring the date sequence is checked
and sorted.

### 6.4 Idempotency

The run is safe to re-trigger from the GitHub Actions UI or from the CLI:

- All storage writes are `INSERT OR REPLACE` on primary key. Duplicate writes are
  no-ops.
- `tbl_run_log.tickers_done` lets the orchestrator skip already-completed tickers.
- `CallBudget` is reloaded from disk on same-day re-run — the call counter does not
  reset.
- The orchestrator exits cleanly on `DailyBudgetExceeded` and can resume on the
  next calendar day (fresh budget, skipping tickers already stored for today's date).

### 6.5 Warmup window handling

The most demanding indicator (MAV Breakout percentile window) requires 250 bars.
`min_history_bars=250` is a universe filter applied before indicators run. In the
orchestrator:

- After fetching history, count valid (non-null `close`) rows.
- If count < 250: log the ticker as `skipped_warmup`, do not run indicators, do not
  write to `tbl_indicator_outputs`. Continue to the next ticker.
- The 15 curated tickers in the narrow `sample` universe are established names with
  years of history. Warmup exclusions will not occur in normal operation.
- A newly added ticker must accumulate 250 bars (~1 year) before it appears in
  indicator output. This is expected and correct.

### 6.6 Failure modes and recovery

| Failure | Detection | Response |
|---------|-----------|----------|
| API 4xx / 5xx | `httpx` raises `HTTPStatusError` | Log ticker + status, mark skipped, continue loop |
| Daily budget exceeded | `DailyBudgetExceeded` raised | Break loop, mark run `partial`, exit 0 |
| Per-minute throughput cap hit | Practically unreachable at 20 calls/day (1,200/min cap confirmed in Probe 2) | Not a concern; no client sleep needed |
| DuckDB write failure | Exception on `execute()` | Log + re-raise; full run failure; GHA marks failure |
| Indicator `compute()` raises | Exception caught per indicator | Log (ticker, indicator, traceback), skip that indicator for that ticker, continue |
| OHLCV validation fails | Sanity check in orchestrator | Log, skip ticker, continue |
| Stale exchange date in bulk response | Date comparison in §6.3 | Skip exchange, log |
| Runner killed mid-run | Detected via `partial` status on next run | Idempotent resume from `tickers_done` checkpoint |

For the test phase, failures are visible in the GitHub Actions log. Alerting
infrastructure (Healthchecks.io, Sentry) is a Phase D deliverable.

### 6.7 Data quality handling

**Sanity checks applied per ticker before indicator computation:**

- `close > 0` for all rows.
- `high >= low` for all rows.
- `volume >= 0` for all rows.
- No duplicate dates.
- Date sequence is monotonically ascending.

Any row failing a check is dropped before storage. If more than 5% of rows in a
history fetch fail any check, the entire ticker is skipped for the run and logged.

**Adjusted close usage:** `adj_close` is the price series passed to all indicator
`compute()` calls (as the `close` column). The unadjusted `close` is stored in
`tbl_prices` for reference but is not used by the indicator engine.

**Large single-day price jumps:** A bar where
`abs(close[t] / close[t-1] − 1) > 0.5` is flagged in the run log for review. It
is not automatically dropped — legitimate gap-ups and limit moves exist — but it is
not silently ignored either.

**Missing bars (exchange holidays, data gaps):** Not treated as errors. Rolling
window indicators handle sparse series. Log the gap counts per ticker per run.

---

## 7. Open decisions

These must be resolved before or during Phase C. Items marked **Day 1** block the
build session that depends on them.

| # | Decision | Stakes | Status |
|---|----------|--------|--------|
| 1 | **Production data sourcing path** (A / B / C / Twelve Data / Tiingo) | Whether `us`/`global` are ever built; client layer architecture | Open — before Phase D |
| 2 | **Exact 15 tickers in the `sample` universe** | What the test phase actually runs on; regional and sector coverage of early outputs | Open — before Session 3 |
| 3 | **Does the EODHD free tier support the bulk-EOD endpoint?** | Daily refresh costs; architecture of the daily loop | **Resolved: NO.** HTTP 423. Per-ticker EOD required. Daily refresh = 15 calls for 15 tickers. (Probe 1) |
| 4 | **Does the EODHD free tier cover non-US exchanges for per-ticker history?** | Whether the narrow universe can include non-US tickers | **Partially open.** US confirmed (Probe 2). Non-US not yet probed. Test-phase universe is US-only until confirmed. |
| 5 | **Does the EODHD symbol-list response include fundamentals (market cap, sector, ADV)?** | Whether fundamentals require separate calls or an external source | **Resolved: NO.** 7 fields only: Code, Name, Country, Exchange, Currency, Type, Isin. See decision #14 for the resulting metadata strategy. (Probe 2) |
| 6 | **ADV computation method** for `min_avg_daily_value` filter | Fundamentals endpoint (1 extra call/ticker) vs. compute from stored price data vs. relax filter initially | Open — before Session 3 |
| 7 | **Exact liquidity filter defaults** (`min_market_cap_usd`, `min_avg_daily_value`, `min_price`, `min_history_bars`) | Universe size and composition | **Fully resolved.** All four confirmed by user decision: `min_market_cap_usd=$750M`, `min_avg_daily_value=$5M`, `min_price=$1.00`, `min_history_bars=250`. |
| 8 | **Daily run time** (back-solve from 6 AM ET delivery, confirmed EODHD publish lag) | GitHub Actions cron time | Open — after first live end-to-end run |
| 9 | **Staging scope names** — keep `sample|us|global` or add a `narrow` scope? | CLI flag surface is carried into Phase D; changing it later is a breaking change | Open — before Session 4 |
| 10 | **Combination registry format for Phase C** — Python constant vs. config file vs. DuckDB table | Affects how Phase D wires combination selection; Python constant is simplest and sufficient for Phase C | Open — before Session 4 |
| 11 | **`raw_value` storage strategy** — full `compute()` dict in JSON, or extracted scalar fields? | Full JSON: flexible, larger, opaque to SQL; extracted: queryable, requires schema migration per indicator change | **Resolved: full JSON dict.** Implemented in `storage.py` (34 tests green). |
| 12 | **(Option B only) Specific sources and conflict resolution rule** | Core to the multi-source architecture; cannot build without this | Open — before any Option B code |
| 13 | **(Option A/B only) Production backfill strategy** — how to acquire 2+ years of global history before Phase D launch | Phase E backtest needs sufficient history; cannot start E without it | Open — before Phase D |
| 14 | **Metadata source strategy** — how to obtain market cap, sector, and region per ticker | Universe loader cannot apply `min_market_cap` or sector-grouping filters without this. See §7.1 for options. | Open — before Session 3 |

### 7.1 Metadata source strategy (decision #14)

The EODHD symbol-list endpoint does not return market cap, sector, region, or average
daily value (confirmed in Probe 2 — see `spec/eodhd-probe-notes.md`). The universe
loader needs these fields to apply the `min_market_cap` and sector-grouping taxonomy.
Five options exist:

**Option A — Manual constants in `universe.py` (test-phase default)**

The `sample` scope hardcodes ~15 tickers. Market cap and sector for these tickers are
known in advance and can be embedded as constants directly in `universe.py`. No API
calls are needed; no external source is required.

- Calls consumed: 0.
- Filtering: `min_market_cap` is guaranteed by manual selection; sector and region are
  embedded constants.
- Limitation: does not scale beyond the manually maintained list.
- **Verdict for test phase: use this.** For `sample`, no other option is needed.

**Option B — EODHD fundamentals endpoint (1 call per ticker)**

```
GET /api/fundamentals/{TICKER}.US?api_token=...&fmt=json
```

Returns full fundamental data including `MarketCapitalization`, `Sector`, `Industry`,
and historical financials. One call per ticker.

- Calls consumed: 1 per ticker on universe build; refreshed periodically (market cap
  drifts; sector rarely changes).
- Feasibility on free tier: 20 calls/day. For 15 tickers, feasible if scheduled as a
  separate metadata session from the daily OHLCV refresh (15 calls/session). For a
  `us` scope (~3k tickers) it requires ~150 sessions of fundamentals calls — not
  feasible on free tier.
- Feasibility on paid tier: viable. `daily_limit` is orders of magnitude higher.
  The fundamentals endpoint is the canonical metadata source under a paid-tier build.
- **Verdict for test phase: feasible but wasteful.** Only use if metadata cannot be
  embedded manually. On paid tier (Option A in §4.1), this is the preferred source.

**Option C — External metadata batch (yfinance or similar)**

Use `yfinance.Ticker(ticker).info` to retrieve market cap and sector for the narrow
universe. This is a one-time or periodic metadata fetch, separate from the OHLCV
pipeline.

- Calls consumed: 0 EODHD calls. Yahoo Finance rate limits apply (unofficial, variable).
- Data quality: adequate for large-cap names; unreliable for less-followed tickers.
  Market cap figures may lag. Sector taxonomy differs from EODHD's.
- Legal: Yahoo Finance TOS prohibits automated data extraction for financial product
  purposes (same concern as multi-source Option B in §4.2).
- **Verdict: viable as a one-off convenience tool for the test phase if tickers are
  well-known names. Not suitable for a production metadata pipeline.**

**Option D — Manual metadata cache file**

Maintain a small JSON or CSV file (e.g. `data/sample_meta.json`) with the metadata
for the 15 tickers. Updated manually when a ticker is added or its sector changes.

- Calls consumed: 0.
- Maintenance: proportional to universe churn. For a stable 15-ticker list, one update
  per year at most.
- **Verdict: simplest possible approach for the test phase. Equivalent to Option A in
  practice — the constants live in a file rather than in source code.**

**Option E — Compute ADV from stored prices; skip market cap programmatic check for narrow universe**

Average daily value (`min_avg_daily_value`) can be computed from stored OHLCV once
data is in `tbl_prices` (rolling 20-day mean of `close × volume`). This replaces any
external ADV field without consuming additional API calls.

Market cap cannot be computed from OHLCV. For the narrow `sample` scope, where all
tickers are manually curated large-cap names, the `min_market_cap=$750M` filter is
trivially satisfied by selection — it need not be enforced programmatically.

- Calls consumed: 0.
- Limitation: provides ADV only; does not supply sector, region, or market cap.
  Those must still come from Options A, B, C, or D.
- **Verdict: useful complement to any of the above. Recommended for ADV computation
  once prices are in storage. Does not replace a metadata source.**

**Recommended strategy for Phase C:**

Use **Option A** (manual constants in `universe.py`) for the `sample` scope. Once
OHLCV is in storage, compute ADV from stored prices (Option E). Defer the
`min_market_cap` programmatic filter for `us`/`global` scopes to the production
sourcing decision (open decision #1). If paid EODHD (§4.1) is chosen for production,
use the fundamentals endpoint (Option B) for metadata at scale.

---

## 8. Suggested build session order

Sequenced for the test phase on EODHD free tier. Sessions 1–6 are the Phase C gate.
If the production sourcing decision is Option A, the transition after session 6 is
a single additional session (remove guard, raise budget ceiling, run `--universe us`
to backfill). If Option B, sessions 3–5 require a parallel multi-source adapter
track — plan that separately before beginning.

---

**Session 1 — Probe and storage schema ✓ COMPLETE**

Both probe sessions are done (Probe 1: 2026-05-16; Probe 2: 2026-05-18). Full
findings in `spec/eodhd-probe-notes.md`. Resolved open decisions #3 (bulk-EOD
blocked), #5 (symbol-list fields), #7 (rate-limit mechanism), #11 (raw_value
strategy). `data/storage.py` is written and green (34 tests). `tbl_scan_results`
dropped; two-layer schema implemented.

Budget consumed: 3 probe calls (2 Probe 1, then 3 Probe 2; 3 total — Probe 1 call
was an HTTP 423 and counted against budget).

---

**Session 2 — EODHD client (per-ticker, daily-budget enforcement)**

Implement `data/eodhd.py` with:

- `CallBudget(daily_limit=20)` — daily counter loaded from `tbl_run_log` on same-day
  re-run; raises `DailyBudgetExceeded` at the limit. No per-request sleep: the
  1,200/minute throughput cap (confirmed in Probe 2) is not a binding constraint at
  20 calls/day.
- `EODHDClient.fetch_history(ticker, exchange, from_date, to_date)` — calls the
  per-ticker EOD endpoint (confirmed available on free tier — Probe 2). Returns the
  canonical DataFrame. Renames `adjusted_close` → `adj_close` on ingest.
- `use_bulk_eod: bool = False` config flag — structure the client so enabling the
  bulk-EOD endpoint on a paid tier is a configuration change, not a rewrite. The
  flag does not activate any code in the free-tier build.

Write `tests/test_eodhd.py` using mocked `httpx` responses: verify the canonical
DataFrame shape is returned, `adjusted_close` is renamed, `DailyBudgetExceeded` is
raised at the limit, and a same-day re-run initialises the counter from the stored
`api_calls_used` value.

Budget consumed: 0 (mocked).

---

**Session 3 — Universe loader, `sample` scope**

Resolve open decisions #2, #6, #7 before this session. Implement `data/universe.py`
with the `sample` scope returning the hardcoded list, all liquidity filter
parameters, and `PaidTierRequired` guards for `us` and `global`. Write
`tests/test_universe.py`: verify the list is returned, filters are applied
correctly, `PaidTierRequired` is raised for non-sample scopes.

Budget consumed: 0 (no API calls in the `sample` scope loader).

---

**Session 4 — Orchestrator and CLI**

Resolve open decisions #9, #10 before this session. Implement `cli.py` and the
pipeline from §5.4. Wire Phase B's `REGISTRY` into the indicator computation step.
Wire `scoring.py` Stage 1 normalisation into the per-ticker loop. Use the hardcoded
combination constant for the three seeded combinations. Write `tests/test_cli.py`
using stored fixture data in an in-memory DuckDB (no API calls). The Phase B
indicator engine is already green — the orchestrator test validates the wiring, not
the math.

Budget consumed: 0 (fixtures only).

---

**Session 5 — End-to-end test on live API**

Run `uv run scanner run-daily --universe sample` against live EODHD data. This
session consumes real API calls — plan for up to 15 calls (one per-ticker EOD
fetch per ticker). Schedule on a day with a fresh budget.

Verify: DuckDB `tbl_prices` contains 250+ rows per ticker; `tbl_indicator_outputs`
contains 11 rows per ticker per date; `tbl_combo_results` contains 3 rows per ticker
per date (one per combination); run log shows `completed`; re-run produces identical
results consuming 0 new calls. Check that a sample of indicator outputs are
directionally plausible for the known tickers.

Budget consumed: up to 17 calls.

---

**Session 6 — Edge-case hardening and full test suite**

All tests use mocks or in-memory DuckDB. No live API calls.

- Inject a budget of 5 into a 15-ticker run; verify `partial` status and clean exit.
- Inject a 5xx on one ticker; verify skip-and-continue and correct run log.
- Feed a DataFrame with stale date in the bulk response; verify exchange-skip logic.
- Feed a DataFrame with >5% invalid rows; verify ticker-skip logic.
- Verify that a re-run on an already-completed date produces zero new writes and
  zero new API calls.
- Finalise `tests/test_storage.py`, `tests/test_eodhd.py`, `tests/test_universe.py`,
  `tests/test_cli.py`.

Budget consumed: 0.

---

**Phase C gate:** `uv run scanner run-daily --universe sample` completes
end-to-end, writes both DuckDB layers correctly, the test suite passes with mocked
API calls, and the run log shows `completed` with ≤ 20 API calls consumed.

**Production transition (post-gate, before Phase D):** Resolve open decision #1.
If Option A: one additional session to remove the `PaidTierRequired` guard, raise
the budget ceiling, and run `--universe us` to backfill. If Option B: 3–4 additional
sessions for the multi-source client layer — plan separately.
