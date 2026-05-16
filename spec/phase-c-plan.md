# Phase C Plan — Data Pipeline

Synthesized from `CLAUDE.md`, `ROADMAP.md`, `spec/universe.md`, `spec/scoring.md`,
and the existing scaffolds in `src/scanner/data/` and `src/scanner/cli.py`.

---

## 1. Scope

### 1.1 What this phase delivers

- `data/storage.py` — complete two-layer DuckDB persistence: per-indicator rows
  `(ticker, exchange, date, indicator_name)` as the source of truth, plus derived
  combo/ranking rows by combination name. Replaces the current incomplete scaffold.
- `data/eodhd.py` — live EODHD client extended with the bulk-EOD daily-refresh
  endpoint and a rate limiter that enforces both daily and per-minute ceilings.
- `data/universe.py` — universe loader with the `sample` scope fully operational
  for the test phase; `us` and `global` scopes guarded behind a paid-tier check.
- `cli.py` — `scanner run-daily --universe sample|us|global` with a full
  orchestration pipeline: load universe → fetch OHLCV → store raw prices → run all
  registered indicators → store per-indicator outputs → store combo + ranking.
- End-to-end gate: `uv run scanner run-daily --universe sample` completes on the
  narrow universe, writes both DuckDB layers, respects both rate limits, and is
  idempotent on re-run.

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

Both limits apply simultaneously to every API call from every component:

| Limit | Value | Consequence of violation |
|-------|-------|--------------------------|
| Daily API calls | **20 / calendar day** | Run aborts; remaining tickers deferred to next day |
| Per-minute call rate | **2 / minute** | 30-second minimum gap between consecutive calls |

These are not soft targets. Every component that makes API calls must thread a shared
`CallBudget` object and a per-minute throttle. Neither limit can be bypassed by
parallelism — all API calls are serialised through the single client.

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

- Historical backfill: 15 per-ticker calls + 1–2 probe/metadata calls = 16–17 calls.
  Done in one session.
- Daily refresh: 1 bulk-EOD call covers the entire exchange; filter to the 15 in
  Python. Daily cost ≤ 4 calls including overhead and error headroom.
- At 2 calls/min: 17 calls takes approximately 8.5 minutes. Well within any
  reasonable run window.
- Expanding by 5 tickers later costs 5 backfill calls, well inside the 16 spare
  daily calls after steady-state.

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
| First-run probe (one-time) | 2 | Day 1 only |
| Historical backfill, 15 tickers (one-time) | 15 | Day 1 only; 7.5 min at 2/min |
| Daily: bulk-EOD for universe exchange(s) | 1–2 | Every run day |
| Daily: metadata / retry / symbol-list refresh | 2 | Every run day |
| **Daily steady-state total** | **≤ 4** | Well within 20/day |
| **Day-1 total** | **≤ 19** | Within 20/day with 1 spare |

After day 1 the daily budget is almost entirely unspent. The 16 spare daily calls
can absorb retries, additional metadata, or incremental universe expansion.

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

### 3.4 EODHD probe — mandatory first step, Day 1

Before writing any parser, make 2 API calls (consuming 2 of the day's 20) to
observe actual response shapes:

1. `GET /eod-bulk-last-day/{exchange}` — bulk EOD shape: field names, types,
   ticker key format, whether adjusted prices are included.
2. `GET /eod/{TICKER}.{EXCHANGE}?period=d&from=...` — per-ticker historical shape.

Document the real shapes in a comment block at the top of `data/eodhd.py`. Do not
infer from documentation or prior versions — EODHD response shapes have changed
across API versions. Build the parsers around what is actually observed.

The probe also answers open decisions #3, #4, and #5 (§7):
- Is the bulk-EOD endpoint available on the free tier?
- Does the free tier cover non-US exchange history?
- Does the symbol-list response include fundamentals (market cap, sector, ADV)?

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

The existing `EODHDClient` is the foundation. Two additions are required:

**1. Bulk-EOD endpoint** (currently missing):

```
GET /eod-bulk-last-day/{exchange}?api_token=...
```

Returns all tickers' last-trading-day OHLCV for an exchange in one call. The parser
converts the response to `dict[str, pd.Series]` keyed by ticker symbol. The exact
field names must be confirmed in the probe session — do not guess.

**2. Rate limiter and daily budget:**

```python
class CallBudget:
    """Shared daily call counter. Persisted across process restarts."""
    def __init__(self, daily_limit: int = 20, persist_path: Path | None = None) -> None: ...
    def charge(self, n: int = 1) -> None: ...   # raises DailyBudgetExceeded if over
    def remaining(self) -> int: ...
    def save(self) -> None: ...                  # write JSON to persist_path
    @classmethod
    def load(cls, persist_path: Path) -> CallBudget: ...   # reload from disk
```

The per-minute throttle lives in the client, not the budget: before each HTTP call,
sleep `max(0, 30.0 - elapsed_seconds_since_last_call)`. The 30-second floor
guarantees ≤ 2 calls/minute. The throttle is not configurable — it is a hard
constraint, not a tunable parameter.

`CallBudget` is persisted to `data/budget_YYYY-MM-DD.json` after every charge. On
re-run within the same calendar day, the orchestrator loads the existing budget
rather than creating a fresh one — preventing a re-run from resetting the counter
and silently making extra calls.

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

- Does the EODHD free tier support the bulk-EOD endpoint? The probe session
  resolves this. If not, daily refresh costs 15 calls instead of 1, consuming
  the entire budget for the narrow universe — a significant constraint change.
- Does the free tier cover non-US exchange history (LSE, TSE, ASX)? If not, the
  narrow universe is US-only.
- Does the EODHD symbol-list response include market cap, sector, and ADV fields?
  If these require separate fundamental calls (one per ticker), the budget
  arithmetic changes materially.
- For Option B: which specific sources form the stack, and what is the conflict
  resolution rule? Both must be decided before any Option B code is written.

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

- Exact ADV computation method: fundamentals endpoint (1 extra call per ticker) vs.
  compute from 20-bar price fetch vs. relax ADV filter to market-cap-only for the
  initial build. (Open decision #6.)
- Does the EODHD symbol-list endpoint return sector and region fields? If not, these
  must be sourced separately or left null until a fundamentals call is added.
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

3. Fetch today's OHLCV — one bulk call before the per-ticker loop
   client.fetch_bulk_today(exchange) for each exchange in the universe.
   budget.charge(1) per exchange.
   Store result as {ticker: today_series} in memory for step 4c.

4. Per-ticker loop
   For each (ticker, exchange) in universe:
     a. Skip if ticker in storage.get_completed_tickers(run_id). (idempotent)
     b. Fetch OHLCV history if not up-to-date in storage.
        client.fetch_history(ticker, exchange, from_date, to_date).
        budget.charge(1). Raises DailyBudgetExceeded → go to step 5.
        storage.write_prices(ticker, exchange, df).
     c. Append today's bar from the bulk result fetched in step 3.
        storage.write_prices(..., today_row).
     d. Validate OHLCV (§6.7 sanity checks).
     e. Warmup check: if row count < min_history_bars, log skip, continue.
     f. Build compute df: read from storage, set adj_close as 'close'.
     g. Run all registered indicators:
          for name, mod in REGISTRY.items():
              result = mod.compute(df)
     h. Normalise each result to 0–1 (Stage 1 of spec/scoring.md).
     i. storage.write_indicator_outputs(rows).
     j. Compute combo score and ranking for each combination in the registry.
        (scoring.py is called here, inline per ticker.)
     k. storage.write_combo_results(df).
     l. storage.log_run_ticker_done(run_id, ticker, exchange).

5. Finalise
   storage.log_run_end(run_id, status='completed'|'partial', api_calls).
   budget.save().
   Print summary: N tickers processed, N signals, N API calls used, remaining budget.
```

**Budget enforcement:** `DailyBudgetExceeded` is caught at the top of the ticker
loop (step 4b). On catch: log remaining tickers, call `log_run_end(status='partial')`,
`budget.save()`, and exit with code 0. A partial run is not a failure — it is the
expected outcome when the narrow universe is being expanded or backfilled. GitHub
Actions should not mark a partial run as a workflow failure.

**Bulk-EOD optimisation:** Step 3 fetches today's data for the entire exchange in
one call, stored as a dict. Step 4c reads from this dict without making additional
calls. For the 15-ticker narrow universe, one exchange call covers all tickers. The
per-ticker loop then costs zero calls for the daily bar — only the history call (4b)
is charged, and only for tickers whose stored data is not current.

**Combination registry:** For Phase C, a Python-level constant list of the three
seeded combinations from `spec/scoring.md` (`default`, `breakout_family`,
`mean_reversion`). The orchestrator iterates this list for step 4j. Phase D may
make this configurable.

**`scoring.py` integration:** The orchestrator calls `scoring.py` inline per ticker
in step 4j. `scoring.py` receives the per-indicator outputs for one ticker (from the
dict built in steps 4g–4h) and a combination definition, and returns the combo score
and ranking fields. It does not read from or write to storage — that is the
orchestrator's job.

**Open questions:**

- Where does the `scoring.py` normalisation logic (Stage 1) live — inside
  `scoring.py` or inline in the orchestrator? Recommended: inside `scoring.py` as
  `normalize(indicator_name, raw_result) -> float`. This keeps the scoring logic in
  one place and makes the orchestrator a thin pipeline.
- Total run time for `--universe sample` at 2/min pacing: estimated 10–12 minutes
  for 15 tickers (including storage writes and indicator computation). Acceptable
  for the test phase. Should be measured in session 5 and logged.

---

## 6. Cross-cutting concerns

### 6.1 API call budget management

`CallBudget` is the single authority on daily consumption. Rules:

1. Instantiated once per run by the orchestrator before any API call.
2. Persisted to `data/budget_YYYY-MM-DD.json` after every `charge()`. On same-day
   re-run, loaded from disk. A re-run that sees an existing budget file for today
   continues from the actual remaining balance — it cannot silently overspend.
3. Every API call in every component goes through `budget.charge()`. The budget
   object is passed as a dependency (not global state) so tests can inject a mock.
4. `DailyBudgetExceeded` causes a clean partial exit. It is not a crash.
5. The per-minute throttle (30-second sleep) is handled inside `EODHDClient`, not
   in the budget. Budget counts calls; client enforces timing.

### 6.2 Historical backfill

**Test phase (15-ticker narrow universe):**

Day 1 fetches the full history for each of the 15 tickers: 15 calls, 7.5 minutes
at 2/min. This is a one-time cost. Subsequent days add only the current bar via the
bulk call. There is no ongoing backfill problem at the narrow scope.

**Extending the narrow universe incrementally:**

Each additional ticker costs 1 backfill call. The 16 spare calls/day after
steady-state absorb up to 16 new tickers per day. Expansion is bounded by the daily
budget, not by architectural constraints.

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

**Stale data detection:** After the bulk-EOD call, compare the most recent date in
the response to the expected prior trading day. If the dates do not match, the
exchange is skipped for this run and flagged in the run log. Never compute indicators
on a bar that may not be the most recent close.

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
| Per-minute throttle | Prevented by client sleep; not raised as an error | N/A |
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

| # | Decision | Stakes | When needed |
|---|----------|--------|-------------|
| 1 | **Production data sourcing path** (A / B / C / Twelve Data / Tiingo) | Whether `us`/`global` are ever built; client layer architecture | Before Phase D |
| 2 | **Exact 15 tickers in the `sample` universe** | What the test phase actually runs on; regional and sector coverage of early outputs | **Before Session 3** |
| 3 | **Does the EODHD free tier support the bulk-EOD endpoint?** | If not, daily refresh costs 15 calls instead of 1; still within 20/day but uses most of the budget | **Day 1 probe** |
| 4 | **Does the EODHD free tier cover non-US exchanges for per-ticker history?** | If not, the narrow universe must be US-only | **Day 1 probe** |
| 5 | **Does the EODHD symbol-list response include fundamentals (market cap, sector, ADV)?** | If not, fundamentals require separate calls — 1 per ticker — materially changing the budget | **Day 1 probe** |
| 6 | **ADV computation method** for `min_avg_daily_value` filter | Fundamentals endpoint (1 extra call per ticker) vs. compute from 20-bar price history vs. relax to market-cap-only for the initial build | Before Session 3 |
| 7 | **Exact liquidity filter defaults** (`min_market_cap_usd`, `min_avg_daily_value`, `min_price`, `min_history_bars`) | `min_market_cap_usd` **resolved: $750M** (user decision). Remaining defaults (`min_avg_daily_value`, `min_price`, `min_history_bars`) are still provisional — confirm before Session 3 | Before Session 3 |
| 8 | **Daily run time** (back-solve from 6 AM ET delivery, confirmed EODHD publish lag) | GitHub Actions cron time | After Day 1 probe |
| 9 | **Staging scope names** — keep `sample|us|global` or add a `narrow` scope? | CLI flag surface is carried into Phase D; changing it later is a breaking change | Before Session 4 |
| 10 | **Combination registry format for Phase C** — Python constant vs. config file vs. DuckDB table | Affects how Phase D wires combination selection; Python constant is simplest and sufficient for Phase C | Before Session 4 |
| 11 | **`raw_value` storage strategy** — full `compute()` dict in JSON, or extracted scalar fields? | Full JSON: flexible, larger, opaque to SQL; extracted: queryable, requires schema migration per indicator change | Before Session 1 |
| 12 | **(Option B only) Specific sources and conflict resolution rule** | Core to the multi-source architecture; cannot build without this | Before any Option B code |
| 13 | **(Option A/B only) Production backfill strategy** — how to acquire 2+ years of global history before Phase D launch | Phase E backtest needs sufficient history; cannot start E without it | Before Phase D |

---

## 8. Suggested build session order

Sequenced for the test phase on EODHD free tier. Sessions 1–6 are the Phase C gate.
If the production sourcing decision is Option A, the transition after session 6 is
a single additional session (remove guard, raise budget ceiling, run `--universe us`
to backfill). If Option B, sessions 3–5 require a parallel multi-source adapter
track — plan that separately before beginning.

---

**Session 1 — Probe and storage schema**

Resolve open decisions #3, #4, #5 (probe the API). Document real response shapes
in `data/eodhd.py`. Write `data/storage.py` from scratch: drop `tbl_scan_results`,
implement the two-layer schema, implement all read/write methods. Write
`tests/test_storage.py` using in-memory DuckDB (no API calls). Resolve open
decision #11 (raw_value strategy).

Budget consumed: 2 probe calls.

---

**Session 2 — Rate-limited EODHD client**

Extend `data/eodhd.py` with `CallBudget`, the per-minute throttle, and
`fetch_bulk_today(exchange)` using the shape confirmed in session 1. Ensure the
per-ticker historical method is also rate-limited. Write `tests/test_eodhd.py`
using mocked `httpx` responses: verify sleep is called, verify budget raises on
overage, verify the canonical DataFrame shape is returned. Resolve open decision #8
(run time) based on probe results.

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
session consumes real API calls — plan for up to 17 calls (15 history + 1 bulk + 1
spare). Schedule on a day with a fresh budget.

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
