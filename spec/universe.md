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
target the "All-World Extended" plan). Key property: the **bulk EOD endpoint**
returns a full day of OHLCV for an entire exchange in **one call** — so a global
daily refresh is ~70 calls, not 80,000. Implement against the bulk endpoint, not
per-ticker calls.

`yfinance` is acceptable for local prototyping (`--universe sample`) but not for the
production global run — it's rate-limited and unreliable at scale.

Read the EODHD bulk-EOD docs before writing `data/eodhd.py` — do not guess the
response shape. Probe the live endpoint once and build the parser around what you
actually observe.

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
| `min_market_cap` | $200M | excludes nano/micro-caps |
| `min_avg_daily_value` | $5M ADV (20-day) | ensures the name is actually tradable |
| `min_price` | $1 | drops penny stocks / data-quality noise |
| `min_history_bars` | 250 | indicators need history; MAV Breakout's percentile window alone is 250 bars |

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
