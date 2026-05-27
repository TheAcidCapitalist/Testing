# Signal Scanner — Roadmap

## What this is

A deterministic global technical scanner with an LLM-generated daily briefing. Every
weeknight it pulls global EOD equity data, runs a fixed set of technical indicators
against the whole universe, ranks the results, and emails a report (plus an Excel
attachment and a live dashboard) by 6 AM ET.

The indicator logic is ported from the *TSC Macro Technical Dashboard (May 2012)*
spreadsheet, originally a Bloomberg-driven macro tool, now adapted for global equities.

## Core architecture principle

**Math decides. LLMs annotate.**

The signal-generation path is fully deterministic and auditable — every signal traces
back to a specific indicator firing on specific data. The LLM never decides which
tickers make the report. It only operates on the *output* of the deterministic
pipeline: explaining, summarizing, contextualizing. If the LLM layer fails, the report
still goes out, just without the briefing.

## Indicator inventory

**Trade indicators (8)** — each emits buy / sell / neutral:

1. RSI (14d, 35/65 cross, 50 exit)
2. Stochastic Oscillator (K=14, D=5) with bullish/bearish divergence requirement
3. MAV Breakout (21/34/55 bands; narrowing + slope turn + stochastic + close-above-band)
4. Daily Trend — Divergence (21d MAV slope, ±0.5% threshold)
5. Daily Trend — Contrarian (mirror of #4)
6. Bollinger Band — Normal (21d Z-score, ±1.5σ)
7. Bollinger Band — Contrarian (mirror of #6)
8. Congestion / Box Breakout — extended horizontal-range detection + breakout
   (see `spec/indicators.md`; the one indicator with no 2012 fixture)

**Confirmation indicators (3)** — filter or demote signals, do not emit buy/sell:

9. MAV Difference Z-Score (20/50 MAV diff, 180d Z, sign-flip = exit)
10. Volatility percentile (180d window; confirms when low)
11. Volume percentile (180d window; confirms when high)

> Open Interest is **dropped** for the equities build — it is a futures/options field
> with no single-stock equivalent. Revisit in v2 with a substitute (put/call ratio or
> short interest) if wanted.

## Combo score & ranking

Combo = weighted mean of indicator values normalized to 0–1 (low = buy, high = sell),
per the 2012 `Read me - Signals` sheet. Bollinger and Box Breakout normalize to the
three-value scheme {0.25 buy / 0.5 neutral / 0.75 sell}. Volume normalizes as
(1 − percentile). Default weights are all 1.0 (arithmetic mean).

A **combination** is a named, data-driven selection of indicators with optional
weights. The default combination covers all 8 trade indicators + Volatility + Volume.
Themed subsets (e.g. `breakout_family`, `mean_reversion`) are seeded in
`spec/scoring.md`. Subset scoring is a query over stored per-indicator outputs — not
a re-run of the indicator engine.

Ranking extends the raw combo with: how many of the N trade indicators in the
selected combination agree on direction, confirmation strength (volatility + volume
percentiles), and breakout recency. **Volume confirmation demotes a low-volume signal
in the ranking — it does not remove it** (v1 decision; volume is an important factor
but not a hard gate).

---

## Build plan

Each phase has a clear gate — don't start the next phase until the current one is green.

### Phase A — Scaffold ✅

**Gate:** `uv run pytest` runs (0 tests collected), `uv run ruff check src tests` passes.

Deliverables:
- [x] `pyproject.toml` — uv, Python 3.12+, deps (pandas, numpy, duckdb, pandas-ta), dev (pytest, ruff)
- [x] `.env.example`, `.gitignore`
- [x] `src/scanner/` package skeleton with `__init__.py` at every level
- [x] `src/scanner/indicators/__init__.py` — auto-discovering registry (safe on empty dir)
- [x] `tests/fixtures/tsc_2012/` — 5 OHLCV CSVs + `expected_indicators.csv`
- [x] `tests/fixtures/synthetic/.gitkeep`
- [x] `tests/conftest.py` — `sample_ohlcv`, `tsc_ohlcv`, `tsc_expected` fixtures
- [x] `spec/` — indicators.md, scoring.md, universe.md, spec-box-breakout.md

---

### Phase B — Indicator engine ✅

**Gate:** all 12 indicator modules green + scoring logic validated. `uv run pytest` all pass.

#### Phase B finish

After all 12 indicator modules are green (8 trade + 3 confirmation + scoring):
- [x] Wire `scoring.py` — normalize, combo score, ranking (per `spec/scoring.md`)
- [x] Verify full suite passes: `uv run pytest` — 319 passed

#### Easy indicators (fixture-validated)

| # | Indicator | Spec | Status | Tests |
|---|-----------|------|--------|-------|
| 1 | RSI | §1 | ✅ | 13 |
| 6–7 | Bollinger Normal + Contrarian | §6–7 | ✅ | 18 |
| 4–5 | Daily Trend Divergence + Contrarian | §4–5 | ✅ | 20 |
| 10 | Volatility (confirmation) | §10 | ✅ | 14 |
| 11 | Volume (confirmation) | §11 | ✅ | 14 |

#### Gnarly indicators (walk through spec logic before coding)

| # | Indicator | Spec | Status | Tests |
|---|-----------|------|--------|-------|
| 3 | MAV Breakout | §3 | ✅ | 23 (5 xfail — data limitation) |
| 2 | Stochastic (divergence-aware) | §2 | ✅ | 17 |
| 8 | Box Breakout | §8 | ✅ | 22 |
| 9 | MAV Difference Z-Score (confirmation) | §9 | ✅ | 23 |

#### Shared modules (private, underscore-prefixed)

| Module | Used by | Status |
|--------|---------|--------|
| `_bollinger_core.py` | Bollinger Normal, Contrarian | ✅ |
| `_daily_trend_core.py` | Daily Trend Divergence, Contrarian | ✅ |
| `_percentile.py` | Volatility, Volume | ✅ |
| `_stochastic_core.py` | Stochastic, MAV Breakout | ✅ |

The 5 xfailed MAV Breakout tests check `breakout_flag` and `days_since_breakout` against the
2012 fixture. These require the original full Bloomberg data history to populate the 250-bar
percentile window correctly — our 287-bar fixture is too short. The formula is correct; the
mismatch is a data availability issue. The synthetic tests are the primary logic validation.

---

### Phase C — Data pipeline ✅ (sample scope)

**Gate:** `uv run scanner run-daily --universe sample` completes end-to-end on 15
curated tickers and writes results to DuckDB.

**Live smoke test — 2026-05-21:** `uv run scanner run-daily --universe sample` completed
successfully. Upgraded to EODHD €19.99 tier (100k calls, 30+ yr history).
Daily budget cap raised to 5000 (runaway protection).
All 15 tickers fetched with full history (ranging from 3,500 to 16,000+ bars),
15/15 post-ingest survivors, 11 indicators per ticker, 15 ranked rows written.
Status: `completed`.

**Prerequisite:** Phase B complete (all indicators green). Do not start data work
until the math is validated — the build order exists so the math is verified before
any live data is involved.

Deliverables:
- [x] `src/scanner/data/eodhd.py` — per-ticker EOD client with `CallBudget` (20/day
      free-tier limit). Bulk-EOD is paywalled (HTTP 423, confirmed); per-ticker endpoint
      confirmed free. Typed exceptions for all HTTP error codes.
- [x] `src/scanner/data/universe.py` — two-stage universe loader. Stage 1: `candidates()`
      returns tickers passing market-cap filter. `us`/`global` raise
      `ProductionScopeUnavailable` (blocked on open decision #14 — metadata source; yfinance
      is one free option). Stage 2: `apply_post_ingest_filters()` filters by
      min_history_bars (250), min_price ($1), min_avg_daily_value ($5M).
- [x] `src/scanner/data/storage.py` — DuckDB two-layer persistence. Layer 1: per-indicator
      rows `(ticker, exchange, date, indicator_name)`. Layer 2: derived combo rows. Upserts
      are idempotent. Run-log tracks budget usage and completed tickers across runs.
- [x] `src/scanner/scoring.py` — `normalize()` maps each indicator's `compute()` dict to
      [0,1]; `score_tickers()` computes weighted combo + rank. Three seeded combinations:
      `default`, `breakout_family`, `mean_reversion`. `mav_diff_z` excluded from all combos.
- [x] `src/scanner/cli.py` — `scanner run-daily --universe sample`. Orchestrates the
      full pipeline: load universe → idempotent fetch loop (budget-aware) → post-ingest
      filter → indicators → score → store → verification dump. Fetch idempotency (today's bar
      already stored → skip). No retry within a run. Single-ticker failure does not crash.
      Daily resolution only (MTF is v2). No report/email/LLM. Verification: top-10 rows
      to stdout or CSV.
- [x] Tests: 160 integration tests (mock EODHD responses, DuckDB writes, universe filters,
      CLI orchestration) — all passing.

**Phase C caveats (recorded, not defects):**
- `us` and `global` scopes deferred — `ProductionScopeUnavailable` guard is in place
  pending open decision #14 (metadata-source strategy for market-cap filtering).
- Multi-timezone exchange close timing deferred to Phase D / Phase C addendum (MTF upgrade).

Deferred from Phase C:
- `us`/`global` scopes — blocked on metadata-source decision (#14). At least one free option
  (yfinance) exists; resolution before Phase C addendum starts.
- Bulk EOD fetch (`use_bulk_eod=True`) — deferred. Per-ticker is sufficient for sample
  scope and currently handles 100k/day budget easily.
- Multi-timezone exchange-close handling — v1 sample scope is US-only, no timezone issue.

---

### Phase D — Deploy + v1 agentic layer ⬅️ next  *(~1 day)*

**Gate:** the nightly run produces an Excel report, sends an email, writes a
dashboard JSON, and the GitHub Actions workflow succeeds.

Deliverables:
- [ ] `src/scanner/report/excel.py` — ranked Excel workbook (top N long, top N short).
- [ ] `src/scanner/report/email.py` — email delivery via Resend API.
- [ ] `src/scanner/report/dashboard_json.py` — `data/latest.json` for the dashboard.
- [ ] `src/scanner/agent/briefing.py` — **v1 agentic layer**. After the deterministic
      pipeline, a Haiku pass over the top-N ranked output produces a one-line "why" per
      ticker plus a 3-paragraph "what looks interesting today" lede. Reads the pipeline's
      JSON output only, never raw data. Fail-soft.
- [x] `dashboard/index.html` — zero-dependency static HTML that reads `dashboard/latest.json`.
      Light/dark mode, Apple-aesthetic design. Renders envelope KPIs, briefing, ranked table.
- [x] `dashboard/latest.json` — sample payload for local testing.
- [x] `.github/workflows/daily-scan.yml` — cron at 08:00 UTC, `workflow_dispatch` for dry runs.
      DB persisted via `actions/cache` (rolling key). Healthchecks.io start/success/fail pings.
      Pages deploy via `actions/deploy-pages`. `email_sent` output gates success vs fail ping.
- [x] Healthchecks.io ping wired — start + success/fail split by `email_sent` flag.
- [x] OpenAI `gpt-4o-mini` briefing layer (was Anthropic Haiku; swapped 2026-05-25).
- [x] Exchange normalisation fix: EODHD symbol list returns `NYSE`/`NASDAQ` but price API
      requires `.US` suffix. All US exchange variants now normalised to `"US"` (2026-05-26).

**Sentry deferred** — Healthchecks fail-ping is the v1 alerting mechanism.

---

### Phase E — Backtest harness  *(post-build; ~1–2 weeks, mandatory)*

**Gate:** provisional defaults replaced with data-driven values.

Deferred until v1 is running on solid principles — **not** dropped. Same `compute()`
functions fed historical bars, measuring forward 1/5/20-day returns per signal and per
combo. This is what tells you which indicators to weight in the combo and how to tune
the Box Breakout parameters. Build it once you have real daily outputs to sanity-check
against.

Deliverables:
- [ ] Backtest harness — runs `compute_series` across historical data, simulates
      entry/exit, measures hit rate and P&L per indicator.
- [ ] Calibrate all provisional defaults:
  - RSI: `buy_cross`, `sell_cross`, `exit_level`
  - Daily Trend: `buy_cross`, `sell_cross`
  - Bollinger: `sd_threshold`, `contrarian_threshold`
  - MAV Breakout: bandwidth percentile threshold, `days_since` window
  - Box Breakout: `min_congestion_bars`, `max_range`, `breakout_buffer`
  - Stochastic: %K/%D windows, divergence lookback
  - MAV Diff Z-Score: `mav1`, `mav2`, `z_history`
  - Scoring weights: `w_agree`, `w_magnitude`, `w_confirm`, `w_staleness`
- [ ] Determine confirmation multiplier values (the 1.0 confirmed / 0.6 demoted
      split in the ranking formula).
- [ ] Document results in `spec/backtest-results.md`.

---

### v2 — Agentic context layer  *(post-backtest)*

A tool-using agent that, for the top-N ranked names, decides what context to pull
(recent news, filings via EDGAR, sector co-movement, persistence vs. recent top
signals) and writes a contextualized briefing. This is the genuinely agentic feature —
build it second, when a month of deterministic outputs exists to compare against.

### v2.5 — Reflective loop  *(optional)*

A weekly cron asks Claude to review the last N days of top signals against what those
tickers actually did, and writes an accumulating `weekly-review.md`. Soft
self-correction — the LLM never gets authority over signals, it just helps spot which
math is working in which regime.

---

## Repo structure

```
signal-scanner/
├── CLAUDE.md
├── HANDOVER.md
├── ROADMAP.md                    ← this file
├── pyproject.toml                ← uv; openai, openpyxl, httpx declared
├── .env.example                  ← EODHD_API_KEY, OPENAI_API_KEY, RESEND_API_KEY, ...
├── .github/workflows/
│   └── daily-scan.yml            ← cron 08:00 UTC + workflow_dispatch; Pages + Healthchecks
├── spec/
│   ├── indicators.md
│   ├── scoring.md
│   ├── universe.md
│   ├── dashboard-json.md         ← canonical output contract (D1)
│   ├── box-breakout-mt.md        ← MTF upgrade spec
│   └── resend-probe-notes.md
├── scripts/
│   └── resend_probe.py
├── src/scanner/
│   ├── indicators/               ← one file per indicator, auto-registered
│   │   ├── __init__.py
│   │   ├── _bollinger_core.py
│   │   ├── _daily_trend_core.py
│   │   ├── _percentile.py
│   │   ├── rsi.py
│   │   ├── bollinger_normal.py
│   │   ├── bollinger_contrarian.py
│   │   ├── daily_trend_divergence.py
│   │   ├── daily_trend_contrarian.py
│   │   ├── volatility.py
│   │   ├── volume.py
│   │   ├── mav_breakout.py
│   │   ├── stochastic.py
│   │   ├── box_breakout.py
│   │   └── mav_diff_z.py
│   ├── data/
│   │   ├── eodhd.py              ← EODHD client
│   │   ├── universe.py           ← Stage 1 candidates + Stage 2 post-ingest filters
│   │   ├── yfinance_meta.py      ← market-cap / sector metadata
│   │   └── storage.py            ← DuckDB wrapper
│   ├── scoring.py                ← combo + rank_score + MTF alignment columns
│   ├── report/
│   │   ├── dashboard_json.py     ← D1 canonical output builder
│   │   ├── excel.py              ← D2 ranked xlsx
│   │   └── email.py              ← D4 Resend sender
│   ├── agent/
│   │   └── briefing.py           ← D3 OpenAI gpt-4o-mini; fail-soft
│   └── cli.py                    ← run_daily + run_report_pipeline + argparse
├── tests/
│   ├── fixtures/
│   │   ├── tsc_2012/
│   │   └── synthetic/
│   └── test_*.py                 ← 432 tests, 0 skipped
├── dashboard/
│   ├── index.html                ← static Pages dashboard; fetches ./latest.json
│   └── latest.json               ← sample payload + replaced by workflow on each run
└── data/                         ← gitignored; DuckDB lives here locally
```

## Tech stack

- **Language:** Python, `uv` for env/deps.
- **Math:** pandas / numpy; `pandas-ta` for standard indicators; MAV Breakout,
  divergence Stochastic, and Box Breakout hand-written.
- **Data:** EODHD (~$20–50/mo, ~70 exchanges, bulk EOD). yfinance for prototyping.
- **Storage:** DuckDB over Parquet.
- **Runtime:*### Multi-timeframe range-breakout upgrade

Derived from `reference/range_breakout_scanner_brief.docx`. **Not blocking v1.**
The v1 daily-only pipeline ships first; MTF is layered in as addenda to Phases B, C,
D, and E. Every v1 component is compatible with the MTF additions — the indicator
stays single-timeframe, the storage gains one column, the scoring gains one term.

### Core design rule

The indicator stays single-timeframe — it takes whatever bar series it is given.
The orchestrator (Phase C) handles resampling and calls the indicator over daily /
weekly / monthly resampled inputs independently. The scoring layer (Phase D) handles
alignment — promoting stocks where the same indicator fires on multiple resolutions
simultaneously. Do not put resampling logic or multi-timeframe awareness inside
any indicator.

Full spec: `spec/box-breakout-mt.md`.

### Phase B addendum — Box Breakout enhanced spec (Stage 1 & 2)

The Box Breakout section of `spec/indicators.md` has been rewritten to incorporate
the brief's richer single-timeframe logic. These changes are not blocking the current
v1 implementation but define the target state for the enhanced build:

- Two parallel detection signals: resistance-zone proximity (Signal A) + volatility
  compression via ATR ratio (Signal B).
- Minimum duration as a percentage of lookback (`min_congestion_pct=0.75`) rather than
  absolute bar count (`min_congestion_bars=15`).
- Enhanced breakout trigger: price condition + optional ATR expansion + optional volume
  expansion + optional trend filter.
- `confirmed_close` mode for weekly/monthly bars.

**Implementation order:** the current v1 tests remain valid. Additional synthetic
fixtures (`pct_duration_threshold.csv`, `vol_expansion_trigger.csv`,
`trend_filter_gated.csv`) are required for the enhanced-spec build.

### Phase C addendum — resampling and per-resolution storage (Stage 2 & 4) ✅

- **Resampling:** `resample_ohlcv(df, freq)` in `cli.py` produces weekly/monthly bar
  series from daily OHLCV using `pandas.resample()`. Drops trailing incomplete period
  (confirmed-close convention).
- **Per-resolution orchestration:** after computing indicators on daily bars, also
  resample and compute Box Breakout for weekly (W-FRI) and monthly (ME) scan modes.
  Results stored per `(ticker, exchange, date, indicator_name, resolution)` and passed
  to scoring as `box_breakout_weekly` / `box_breakout_monthly` namespaced keys.
- **Storage schema:** `resolution VARCHAR DEFAULT 'daily'` added to
  `tbl_indicator_outputs`. V1 rows are unaffected.

### Phase D addendum — multi-timeframe alignment scoring (Stage 4) ✅

- MTF alignment term in `scoring.py`: weekly+monthly BB only (daily excluded — already
  in combo). `_W_MTF = 0.10` (provisional, Phase E calibrates). ≥2 gate means both
  weekly and monthly must be present for any bonus; dormant for stocks lacking ~20y
  history.
- Rule A: align to combo direction (not daily BB).
- Rule B: daily excluded from numerator/denominator; both weekly+monthly required.
- All 9 OPEN[#n] flags in `spec/box-breakout-mt.md` resolved.

### Phase E backfill implications (Stage 3)

- Run the backtest separately for each scan mode (`short_term`, `medium_term`,
  `long_term`) and measure forward returns at timeframe-appropriate horizons (e.g.
  20 days for short-term, 1 year for long-term).
- Measure whether multi-timeframe alignment signals outperform single-resolution
  signals — this is the central hypothesis from the brief.
- Tune `w_mtf` alongside the other ranking weights.
- Tune Box Breakout enhanced-spec parameters: `min_congestion_pct`, `atr_expansion_factor`,
  `vol_expansion_factor`, `trend_filter_window` per scan mode.

### Open questions — all resolved ✅

| # | Resolution | Spec location |
|---|----------|--------------|
| OPEN[#1] | Volume not gated; stays confirmation #11 | `spec/box-breakout-mt.md` |
| OPEN[#2] | AND (proximity + compression required) | `spec/box-breakout-mt.md` |
| OPEN[#3] | Fixed-lookback %-duration | `spec/box-breakout-mt.md` |
| OPEN[#4] | Symmetric (bull + bear) | `spec/box-breakout-mt.md` |
| OPEN[#5] | Orchestrator owns per-resolution params | `spec/box-breakout-mt.md` |
| OPEN[#6] | Confirmed-close; drop trailing incomplete bar | `spec/box-breakout-mt.md` |
| OPEN[#7] | Align to combo direction; weekly+monthly only | `spec/box-breakout-mt.md` |
| OPEN[#8] | Daily BB replaces old in combo; W/M feed alignment only | `spec/box-breakout-mt.md` |
| OPEN[#9] | History capped at EODHD; 20y/monthly is long mode | `spec/box-breakout-mt.md` |

---

## Open items

| # | Item | Priority |
|---|------|----------|
| OI-1 | Box Breakout default parameters — calibrate in Phase E, not by eye | Phase E |
| OI-2 | `_W_MTF = 0.10` — provisional; Phase E calibrates on long-history subset | Phase E |
| OI-3 | US metadata cache (yfinance) fills incrementally — first few `us` runs will have thin scored sets; normal | Ongoing |
| OI-4 | `actions/cache` for DuckDB is best-effort (evictable after 7 days). Upgrade to S3/R2 if history loss becomes a problem | Post Phase E |
| OI-5 | No CI workflow yet (`.github/workflows/ci.yml`) — PRs aren't lint/test gated | Nice to have |
| OI-6 | v2 OI substitute — put/call ratio vs. short interest | v2 |

---

## What's next (immediate priority order)

### 1. Confirm the dry run ✅ / 🔄
- Trigger `workflow_dispatch` on GitHub Actions.
- Confirm: workflow green, email delivered, `latest.json` published, Pages dashboard renders, Healthchecks pings recorded.
- **Exchange normalisation fix shipped 2026-05-26** — this was the root cause of 0 tickers scoring. Re-run should now score live tickers.

### 2. Let the cache warm up (Days 1–7)
- The yfinance metadata cache (`tbl_universe`) fills incrementally — 500 tickers/run.
- After 3–5 runs the metadata coverage will be broad enough to score a representative US universe.
- Small result sets on early runs are expected and correct.

### 3. Phase E — Backtest & Calibration
- Build `src/scanner/backtest.py`: feed `compute_series` historical bars, simulate entry/exit, measure 1/5/20-day forward returns per signal.
- Calibrate all provisional defaults (RSI thresholds, Bollinger SD, Box Breakout params, scoring weights `_W_AGREE`, `_W_MTF`, etc.).
- Document results in `spec/backtest-results.md`.
- **Gate:** provisional defaults replaced with data-driven values.

### 4. v2 — Agentic context layer *(post-backtest)*
- Tool-using agent for the top-N ranked names: news, filings (EDGAR), sector co-movement.
- Build second, when ≥30 days of deterministic outputs exist to compare against.

---

## Progress summary

| Phase | Status | Tests |
|-------|--------|-------|
| A — Scaffold | ✅ complete | 0 (plumbing) |
| B — Indicators + scoring | ✅ complete | 319 passed |
| C — Data pipeline | ✅ complete (sample scope) | 160 |
| C addendum — Box Breakout MTF | ✅ complete (Stage 4) | 55 (in test_cli.py) |
| D — Deploy + v1 agent | ✅ complete | 71 (24 JSON + 16 Excel + 14 briefing + 14 email + 3 pipeline) |
| D hotfix — Exchange normalisation | ✅ shipped 2026-05-26 | (covered by test_universe.py) |
| E — Backtest & calibration | ⬜ not started | — |
| v2 — Agentic context | ⬜ not started | — |
| v2.5 — Reflective loop | ⬜ not scoped | — |

**Total: 432 tests pass, 0 skipped, 5 xfailed (MAV Breakout fixture — data limitation, documented).**

Last updated: 2026-05-27.
