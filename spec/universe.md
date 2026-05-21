# Universe Spec

Implementation contract for `src/scanner/data/universe.py`. Defines *which*
securities the scanner runs over, where their data comes from, and how they're
filtered and staged.

## Scope decision

**Truly global equities.** The original 2012 dashboard ran ~150 instruments that
were almost entirely futures, FX pairs, government-bond yields, and rates/swaps —
its "Equity" entries were equity *index futures*, not single stocks. The new build
discards that universe entirely and scans **individual listed equities across global
exchanges**.

Realistic numbers: the full global listed-equity universe is ~60–80k tickers across
~70 exchanges. The *tradable* subset after liquidity filtering is far smaller —
roughly 8–15k names. The scanner ingests broadly and filters down; see "Liquidity
filters" below.

## Data source

**EODHD** (eodhd.com) — chosen for global coverage at low cost (~$20–50/mo,
target the "All-World Extended" plan).

**Free tier (test phase):** The bulk-EOD endpoint is blocked (HTTP 423) on the free
tier (confirmed in Probe 1, 2026-05-16). The per-ticker EOD endpoint is available
(confirmed in Probe 2, 2026-05-18). The free tier imposes a hard **20 calls/day**
billing-layer quota. This limits the test-phase universe to ~15 tickers — see
`spec/phase-c-plan.md §3` for the full budget arithmetic.

Per-ticker EOD endpoint:
```
GET /api/eod/{TICKER}.{EXCHANGE}?api_token=...&period=d&from=YYYY-MM-DD&fmt=json
```
Returns a JSON array of OHLCV bars. Fields per bar: `date` (YYYY-MM-DD string),
`open`, `high`, `low`, `close`, `adjusted_close` (NOT `adj_close`), `volume`
(integer). The client renames `adjusted_close` → `adj_close` on ingest.

**Paid tier (production):** The bulk-EOD endpoint becomes available. It returns all
tickers for an exchange in one call (~70 calls covers the full global universe per
day). Transitioning from the free-tier client to the paid-tier client is a
configuration change — raise `daily_limit`, set `use_bulk_eod=True` in the client,
provide a paid API key. No code rewrite. See `spec/phase-c-plan.md §4` for the
full production sourcing decision.

For metadata (market cap, sector, region), `yfinance` is the designated source for
the `us` and `global` scopes (resolved in decision #14). While not suitable for the
high-volume OHLCV pipeline, it is an acceptable, free path for metadata lookups.

Full probe findings are in `spec/eodhd-probe-notes.md`.

## What we ingest per ticker

The standard OHLCV bundle every indicator consumes:
`date, open, high, low, close, volume`.

Plus metadata for grouping and filtering: `exchange, country/region, sector,
currency, market_cap, average_daily_value`.

**Not ingested:** Open Interest (dropped — see `indicators.md`), realized-volatility
tickers, roll-adjusted futures tickers. Those were Bloomberg/futures concepts from
the original sheet with no equities equivalent. Realized volatility for the
Volatility confirmation indicator is **computed** from the close series, not
downloaded.

## Liquidity filters

Applied in the universe layer, **before** indicators run, so the engine never wastes
compute on untradable microcaps. These are the only hard filters in the system
(scoring-layer volume confirmation merely *demotes* — see `scoring.md`).

| Filter | Default | Rationale |
|--------|---------|-----------|
| `min_market_cap` | $750M | excludes small-caps; settled by user decision, not a placeholder |
| `min_avg_daily_value` | $5M ADV (20-day) | ensures the name is actually tradable; settled by user decision, not a placeholder |
| `min_price` | $1.00 | drops penny stocks / data-quality noise; settled by user decision, not a placeholder |
| `min_history_bars` | 250 | indicators need history; MAV Breakout's percentile window alone is 250 bars; settled by user decision, not a placeholder |

All are parameters. Tune later, but start conservative — a tighter universe produces
a more useful report than a 60k-row dump.

## Asset-class / grouping taxonomy

The original sheet grouped instruments as Comm / Fx / Fi / Equity / Swaps. The
equities build replaces that with an equities-native taxonomy used for report
grouping and dashboard filtering:

- **region** — e.g. North America, Europe, UK, Japan, Asia-ex-Japan, EM
- **country** — exchange country
- **exchange** — the listing exchange (e.g. NASDAQ, LSE, TSE)
- **sector** — GICS-style sector

The report and dashboard let the user slice top signals by region and sector, the
same way the original dashboard sliced by asset class.

## Staging

The universe loader supports three named scopes so development never burns the full
API quota or compute budget:

| Scope | Size | Use |
|-------|------|-----|
| `sample` | ~10 hand-picked liquid tickers | local dev, fast iteration, fixture-adjacent sanity checks |
| `us` | US listings passing the liquidity filters (~3–4k) | mid-stage testing, full pipeline shakeout |
| `global` | all exchanges passing the filters (~8–15k) | production daily run |

CLI: `python -m scanner run-daily --universe sample|us|global`.

## Timezone / close-timing

Global exchanges close at different UTC times. The nightly run must either wait for
the last relevant close or gracefully skip exchanges whose EOD data isn't published
yet — never compute indicators on a half-stale bar.

Back-solve the GitHub Actions cron time from the "6 AM ET delivery" requirement:
all required exchange closes for the prior session must have published their EOD
data before the run starts. A start around 04:00 ET comfortably clears prior-day
closes for the Americas, Europe, and Asia and leaves room for ingestion + compute +
report render before 06:00 ET. Confirm exact EODHD publish lag during Phase C.

## Idempotency

Storage writes (`data/storage.py`) are upserts keyed by `(ticker, date)`. A run that
fails partway and is re-triggered picks up cleanly without duplicating or corrupting
data. This matters because the GitHub Actions runner can be re-run from the UI.

## Metadata dependency

The EODHD symbol-list endpoint does not return market cap, sector, region, or average
daily value (confirmed in Probe 2 — see `spec/eodhd-probe-notes.md`). The liquidity
filters above therefore cannot be applied from the symbol list alone. The resolution
strategy depends on scope:

- **`sample` scope (test phase):** Metadata is embedded as constants in `universe.py`.
  Market cap is guaranteed by manual curation; sector and region are hardcoded per
  ticker. No external source or additional API calls required.
- **`us` / `global` scopes (production):** Metadata must come from the EODHD
  fundamentals endpoint (1 call/ticker, viable on paid tier) or an external source.
  This is an open decision — see `spec/phase-c-plan.md §7.1` for the full option
  analysis and `spec/phase-c-plan.md §7` decision #14.

ADV (`min_avg_daily_value`) can be computed from stored OHLCV once price data is in
`tbl_prices` (rolling 20-day mean of `close × volume`), removing the need for any
external ADV source once the first backfill is complete.
