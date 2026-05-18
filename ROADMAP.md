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

### Phase B — Indicator engine ⬅️ current  *(~1.5–2 days)*

**Gate:** all indicators green against fixtures + synthetics. `uv run pytest` all pass.

One indicator per focused session. Test-first: write the fixture test, then implement
until green. Commit per indicator. Update CLAUDE.md in the same commit.

#### Easy indicators (fixture-validated)

| # | Indicator | Spec | Status | Tests |
|---|-----------|------|--------|-------|
| 1 | RSI | §1 | ✅ done | 13 |
| 6–7 | Bollinger Normal + Contrarian | §6–7 | ✅ done | 18 |
| 4–5 | Daily Trend Divergence + Contrarian | §4–5 | ✅ done | 20 |
| 10 | Volatility (confirmation) | §10 | ✅ done | 14 |
| 11 | Volume (confirmation) | §11 | ✅ done | 14 |

#### Gnarly indicators (walk through spec logic before coding)

| # | Indicator | Spec | Status | Notes |
|---|-----------|------|--------|-------|
| 3 | MAV Breakout | §3 | ⬜ next | 4 simultaneous conditions: bandwidth percentile < threshold, close breaks above/below the band, days-since counter. Fixture has `mav_narrow_pct`, `mav_breakout_flag`, `mav_days_since`. |
| 2 | Stochastic (divergence-aware) | §2 | ⬜ | Non-standard: fires only when %K/%D cross coincides with a price-vs-oscillator divergence. No 2012 fixture — synthetic only. |
| 8 | Box Breakout | §8 | ⬜ | No fixture. O(n) congestion-zone detector. 6 synthetic cases required (see spec). |
| 9 | MAV Difference Z-Score (confirmation) | §9 | ⬜ | 20/50 MAV diff z-scored over 180d. Sign-flip = exit signal. Wire in but does not affect live ranking in v1 (backtest exit only). No fixture column — synthetic validation. |

#### Shared modules (private, underscore-prefixed)

| Module | Used by | Status |
|--------|---------|--------|
| `_bollinger_core.py` | Bollinger Normal, Contrarian | ✅ |
| `_daily_trend_core.py` | Daily Trend Divergence, Contrarian | ✅ |
| `_percentile.py` | Volatility, Volume | ✅ |

#### Phase B finish

After all 12 indicator modules are green (8 trade + 3 confirmation + scoring):
- [ ] Wire `scoring.py` — normalize, combo score, ranking (per `spec/scoring.md`)
- [ ] Write `tests/test_scoring.py` — combo score on known indicator outputs
- [ ] Verify full suite passes: `uv run pytest`

---

### Phase C — Data pipeline  *(~1 day)*

**Gate:** `uv run scanner run-daily --universe sample` completes end-to-end on ~10
hand-picked tickers and writes results to DuckDB.

**Prerequisite:** Phase B complete (all indicators green). Do not start data work
until the math is validated — the build order exists so the math is verified before
any live data is involved.

Deliverables:
- [ ] `src/scanner/data/eodhd.py` — bulk EOD endpoint client, one call per exchange.
      Read the EODHD docs first — don't guess the response shape.
- [ ] `src/scanner/data/universe.py` — universe loader with `sample|us|global` scopes
      and liquidity filters (`min_market_cap=$200M`, `min_avg_daily_value=$5M`,
      `min_price=$1`, `min_history_bars=250`).
- [ ] `src/scanner/data/storage.py` — DuckDB persistence. Two layers of storage:
      (1) **per-indicator rows** keyed by `(ticker, indicator_name, date)` — the
      normalized value and raw indicator result for every indicator on every run;
      (2) **ranked combo rows** keyed by `(ticker, combination_name, date)` — the
      derived combo score and rank. Layer (1) is the source of truth; Layer (2) is
      recomputeable from it. Upserts at both layers are idempotent. This separation
      is what makes subset combination selection a query, not a re-run.
- [ ] `src/scanner/cli.py` — `scanner run-daily --universe sample|us|global`.
      Pipeline: load universe → fetch prices → compute indicators → score → store.
- [ ] Handle multi-timezone exchange closes — the run must wait for / gracefully skip
      stale data.
- [ ] Tests: mock EODHD responses, verify DuckDB writes, end-to-end on sample universe.

Key decisions deferred to this phase:
- Exact EODHD publish lag (confirms the 04:00 ET cron start time).
- Exchange close-timing logic (skip exchanges whose EOD data isn't published yet).
- `yfinance` fallback for `--universe sample` (local dev without API key).

---

### Phase D — Deploy + v1 agentic layer  *(~1 day)*

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
- [ ] `dashboard/artifact.html` — zero-dependency static HTML that reads
      `data/latest.json`. Dark mode, filterable by region/sector.
- [ ] `.github/workflows/daily-scan.yml` — cron at 04:00 ET, full global run.
- [ ] `.github/workflows/ci.yml` — PR tests + lint.
- [ ] Healthchecks.io ping + Sentry on errors.

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
├── README.md
├── ROADMAP.md                    ← this file
├── pyproject.toml                ← uv
├── .env.example
├── .github/workflows/
│   ├── daily-scan.yml
│   └── ci.yml
├── reference/
│   └── tsc-macro-dashboard-2012-05-31.xlsm
├── spec/
│   ├── indicators.md             ← all 11 + box breakout
│   ├── scoring.md
│   └── universe.md
├── src/scanner/
│   ├── indicators/               ← one file per indicator, auto-registered
│   │   ├── __init__.py           ← registry
│   │   ├── _bollinger_core.py    ← shared z-score + days_in_band (private)
│   │   ├── _daily_trend_core.py  ← shared ma_slope_series (private)
│   │   ├── _percentile.py        ← shared Excel PERCENTRANK helper (private)
│   │   ├── rsi.py
│   │   ├── bollinger_normal.py
│   │   ├── bollinger_contrarian.py
│   │   ├── daily_trend_divergence.py
│   │   ├── daily_trend_contrarian.py
│   │   ├── volatility.py
│   │   ├── volume.py
│   │   ├── mav_breakout.py       ← gnarly
│   │   ├── stochastic.py         ← gnarly
│   │   ├── box_breakout.py       ← gnarly, no fixture
│   │   └── mav_diff_z.py         ← confirmation, no fixture
│   ├── data/
│   │   ├── eodhd.py
│   │   ├── universe.py
│   │   └── storage.py
│   ├── scoring.py
│   ├── report/
│   │   ├── excel.py
│   │   ├── email.py
│   │   └── dashboard_json.py
│   ├── agent/                    ← LLM lives here, only here
│   │   └── briefing.py           ← v1; context.py + tools/ come in v2
│   └── cli.py
├── tests/
│   ├── fixtures/
│   │   ├── tsc_2012/             ← extracted ground-truth
│   │   └── synthetic/            ← hand-built cross scenarios
│   └── test_*.py
├── dashboard/
│   └── artifact.html
└── data/                         ← gitignored, local DuckDB
```

## Tech stack

- **Language:** Python, `uv` for env/deps.
- **Math:** pandas / numpy; `pandas-ta` for standard indicators; MAV Breakout,
  divergence Stochastic, and Box Breakout hand-written.
- **Data:** EODHD (~$20–50/mo, ~70 exchanges, bulk EOD). yfinance for prototyping.
- **Storage:** DuckDB over Parquet.
- **Runtime:** GitHub Actions scheduled workflow (free tier covers it).
- **Delivery:** Resend email + Excel attachment + static HTML dashboard.
- **LLM:** Haiku for the briefing layer.
- **Ops:** Healthchecks.io + Sentry.

## Validation strategy

- Indicators with a 2012 fixture → must reproduce `expected_indicators.csv` within
  tolerance (continuous: abs_tol 1e-3; flags & counters: exact).
- Box Breakout → synthetic fixtures in `tests/fixtures/synthetic/` (no 2012 ground
  truth) + informal eyeball on the real TSC fixtures.
- MAV Diff Z-Score → synthetic validation (no fixture column).
- Pipeline → `--universe sample` (10 tickers) for dev before scaling.

## Multi-timeframe range-breakout upgrade

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

Full spec: `spec/multi-timeframe.md`.

### Phase B addendum — Box Breakout enhanced spec

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
- Five open questions requiring user resolution before implementation (see below).

**Implementation order:** the current v1 tests remain valid. Additional synthetic
fixtures (`pct_duration_threshold.csv`, `vol_expansion_trigger.csv`,
`trend_filter_gated.csv`) are required for the enhanced-spec build.

### Phase C addendum — resampling and per-resolution storage

- **Resampling:** add a `resample_ohlcv(df, resolution)` utility in `data/` that
  produces weekly/monthly/quarterly bar series from daily OHLCV using
  `pandas.resample()`. See `spec/multi-timeframe.md §Resampling`.
- **Per-resolution orchestration:** after computing indicators on daily bars, also
  resample and compute for the `medium_term` (weekly) and `long_term` (monthly) scan
  modes. Store per `(ticker, exchange, date, indicator_name, resolution)`.
- **Storage schema change:** add `resolution VARCHAR DEFAULT 'daily'` to
  `tbl_indicator_outputs`. V1 rows are unaffected.
- **Long-history data source decision:** `long_term` (monthly, 240 bars, ~22 years)
  likely requires Stooq or Norgate in addition to EODHD. Decision required before
  Phase C addendum build. See `spec/multi-timeframe.md §Data source implications`.

### Phase D addendum — multi-timeframe alignment scoring

- Add `mtf_alignment_score` computation to `scoring.py`: for each ticker, query
  `tbl_indicator_outputs` across resolutions and count directional agreements.
- Add `w_mtf * mtf_alignment_score` term to the `rank_score` formula.
- Report and dashboard changes: surface multi-resolution alignment as a column or
  badge in the output (e.g. "D+W+M" for daily + weekly + monthly alignment).
- Open question #7 (alignment scoring shape) must be resolved before this build.

### Phase E backfill implications

- Run the backtest separately for each scan mode (`short_term`, `medium_term`,
  `long_term`) and measure forward returns at timeframe-appropriate horizons (e.g.
  20 days for short-term, 1 year for long-term).
- Measure whether multi-timeframe alignment signals outperform single-resolution
  signals — this is the central hypothesis from the brief.
- Tune `w_mtf` alongside the other ranking weights.
- Tune Box Breakout enhanced-spec parameters: `min_congestion_pct`, `atr_expansion_factor`,
  `vol_expansion_factor`, `trend_filter_window` per scan mode.

### Open questions requiring user resolution (before Phase B/C/D addendum builds)

| # | Question | Spec location |
|---|----------|--------------|
| 1 | Resistance zone vs. breakout level (max-high, max-high + buffer, or Signal A touch ≠ breakout) | `spec/indicators.md §8` |
| 2 | AND vs OR for the two parallel detection signals | `spec/indicators.md §8` |
| 3 | Per-bar definition of "true congestion" for minimum-duration counting | `spec/indicators.md §8` |
| 4 | ATR expansion threshold (multiple, baseline window, hard gate vs demotion) | `spec/indicators.md §8` |
| 5 | Confirmed-close vs in-progress mode default | `spec/indicators.md §8` |
| 6 | Bar-count table inconsistency (3–10 yr weekly = 520 bars > 300-bar guideline) | `spec/multi-timeframe.md` |
| 7 | MTF alignment scoring shape (additive, multiplicative, hard-tier, bonus-at-2) | `spec/multi-timeframe.md` |
| 8 | Volume in breakout trigger (Option A: keep separate; B: integrate; C: soft gate) | `spec/indicators.md §8` |

---

## Open items

- Box Breakout default parameters — set in Phase E, not by eye.
- Box Breakout enhanced-spec open questions #1–8 — see table above; resolve before Phase B addendum.
- v2 OI substitute — put/call ratio vs. short interest, decide later.
- Exact daily run time — back-solve from "6 AM ET delivery" once exchange close
  timings are mapped in Phase C.
- Long-history data source for `long_term` scan mode — Stooq vs Norgate; resolve before Phase C addendum.

---

## Progress summary

| Phase | Status | Tests |
|-------|--------|-------|
| A — Scaffold | ✅ complete | 0 (plumbing) |
| B — Indicators | 🔄 in progress (7/12 modules, 5/8 trade + 2/3 confirm) | 79 |
| C — Data pipeline | ⬜ not started | — |
| D — Deploy + v1 agent | ⬜ not started | — |
| E — Backtest | ⬜ not started | — |
| v2 — Agentic context | ⬜ not started | — |
| v2.5 — Reflective loop | ⬜ not scoped | — |
