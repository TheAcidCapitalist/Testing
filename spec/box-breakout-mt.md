# Box Breakout — Multi-Timeframe Upgrade Spec

This document details the multi-timeframe (MTF) upgrade to the Box Breakout scanner (Indicator #8). It is derived from the `reference/range_breakout_scanner_brief.docx` target design, overlaid onto our deterministic 3-layer architecture.

## 1. Architectural Separation

The scanner remains monolithic, with responsibilities cleanly decomposed across three existing layers:

- **Indicator Layer (`src/scanner/indicators/box_breakout.py`)**: Owns the richer single-timeframe detection logic. It remains completely unaware of wall-clock time, resolution, or the existence of other timeframes. It runs on whatever sequence of bars it receives.
- **Orchestrator Layer (`src/scanner/cli.py` & data tools)**: Owns the multi-timeframe mechanism. It resamples the base daily OHLCV into weekly, monthly, and quarterly bars, and invokes the Box Breakout indicator once per resolution.
- **Scoring Layer (`src/scanner/scoring.py`)**: Owns the alignment logic. It promotes tickers that fire simultaneously on multiple resolutions by consuming the per-resolution storage outputs.

---

## 2. Resolution / Lookback Mapping

The core design rule of the MTF scanner is that bar resolution must scale with lookback length to maintain roughly **40–300 bars per analysis window**.

The Orchestrator implements the following target mappings:
- **1–3 months** -> Daily resolution (20–60 bars)
- **3–12 months** -> Daily resolution (60–250 bars)
- **1–3 years** -> Weekly resolution (50–150 bars)
- **3–10 years** -> Weekly or Monthly resolution (36–520 bars)
- **10–50+ years** -> Monthly or Quarterly resolution (40–600 bars)

### Scan Modes
The scanner runs as a parameterized function with three primary default modes handled by the Orchestrator:
1. **Short-term mode**: 60-day lookback on **Daily** bars.
2. **Medium-term mode**: 2-year lookback on **Weekly** bars (~104 bars).
3. **Long-term mode**: 20-year lookback on **Monthly** bars (~240 bars).

*(See OPEN[#9] regarding the data history constraint for multi-decade monthly scans).*

---

## 3. Resampling Contract

**Owner**: Orchestrator (`src/scanner/data/`)

The Orchestrator uses `pandas.DataFrame.resample()` to convert daily OHLCV into higher timeframes before invoking the indicator:
- **Weekly**: `'W-FRI'` (week ending Friday)
- **Monthly**: `'ME'` (month-end)
- **Quarterly**: `'QE'` (quarter-end)

Aggregation rules: Open = `first()`, High = `max()`, Low = `min()`, Close = `last()`, Volume = `sum()`.
The resampled DataFrame is passed directly to the indicator's `compute(df, **params)` function. 
*(See OPEN[#6] regarding handling of in-progress incomplete bars vs confirmed closed bars).*

---

## 4. Storage Resolution Key

**Owner**: Storage (`src/scanner/data/storage.py`)

The `tbl_indicator_outputs` table adds a `resolution` dimension to its primary key:
`(ticker, exchange, date, indicator_name, resolution)`

- `resolution` ∈ `{'daily', 'weekly', 'monthly', 'quarterly'}`.
- Existing v1 daily outputs default to `resolution = 'daily'`.
- The `date` reflects the resampled bar's close date.

---

## 5. Alignment-Scoring Approach

**Owner**: Scoring (`src/scanner/scoring.py`)

The scoring layer queries `tbl_indicator_outputs` across the `resolution` dimension for the same ticker, date, and indicator. It detects **Multi-Timeframe Alignment** when the Box Breakout fires in the same direction on multiple resolutions simultaneously.
*(See OPEN[#7] regarding the computation formula for this promotion).*

## Appendix: Resolved Decisions

- **Volume Integration vs. Separation**: Volume is not gated. Volume stays confirmation indicator #11 (demote, don't kill). The indicator exposes a `volume_expansion: bool` output extra (volume ≥ vol_mult × trailing average) but it does not affect direction.
- **Resistance-zone AND Compression vs. OR**: AND. A congestion window requires resistance-zone proximity and volatility compression simultaneously.
- **Minimum Duration Algorithm**: Fixed-lookback %-duration. Analyse a fixed lookback window of `lookback` bars; congestion must hold across ≥ `duration_pct` of those bars.
- **Direction Symmetry**: Symmetric. Bull breakout above the resistance ceiling → buy (+1); bear breakdown below the support floor → sell (-1).
- **Tolerance Tables Ownership**: Resolution-agnostic params. The indicator hardcodes no per-timeframe tables. It receives `lookback`, `touch_tolerance`, `compression_threshold`, etc. as plain parameters; the orchestrator supplies resolution-scaled values.
- **Bar Mode**: Confirmed-close. Operate on completed bars. Expose a mode param defaulting to "confirmed"; "in_progress" may be stubbed/deferred.
- **Alignment Promotion Computation**: (Stage 4) alignment = additive + w_align * (resolutions_aligned / n_resolutions), same-direction required.
- **Combo Replacement**: (Stage 4) the new indicator's daily resolution replaces the old Box Breakout as the single combo member; weekly/monthly feed only alignment.
- **History-Depth Constraint**: History cap at available EODHD history; "long" mode = 20y/monthly. Stooq documented as a future option, not built.

## Provisional Configuration

The following parameters are marked as provisional (set by eye to get the pipeline flowing, final values to be calibrated from the Phase E backtest):
- **compression_threshold**: Replaced ATR_short/ATR_long ratio with a scale-invariant Range-to-ATR window-level metric. Provisional values: 5.0 (daily), 6.0 (weekly), 8.0 (monthly). A true base spans fewer ATRs than a trending random walk.
- **touch_tolerance**: Scaled per mode to accommodate expected base height relative to extreme price range. Provisional: 0.05 (daily), 0.15 (weekly), 0.30 (monthly).
- **duration_pct**: 0.70 for daily/weekly, 0.50 for monthly. A 20-year lookback is highly unlikely to trade purely sideways for 168+ months continuously, so 0.50 opens detection for 10-year consolidations within the 20-year window.

## Stage 4 Alignment Scoring Rules
- **Rule A (Alignment Target)**: Align to the overall combo candidate direction (not the daily Box Breakout). If the stock is selected as a candidate (direction != "neutral"), weekly and monthly resolutions are checked against this combo direction. This reinterprets OPEN[#7] to ensure we confirm the holistic trade thesis, not just the single-indicator signal.
- **Rule B (Scope & Denominator)**: The alignment term measures *only* weekly and monthly cross-timeframe confirmation. Daily BB is excluded — it already drives `combo_score` and `agreement_count` via the combo, so including it would double-count. `n_resolutions` and `resolutions_aligned` iterate `["box_breakout_weekly", "box_breakout_monthly"]` only. The ≥ 2 gate means both weekly and monthly must be present for any bonus; a stock with only one MTF resolution available receives 0 bonus (not penalized, just no kicker). The kicker is therefore dormant for stocks lacking ~20 years of history (needed for 240-bar monthly lookback); Phase E calibrates `_W_MTF` on the long-history subset where it is active.
