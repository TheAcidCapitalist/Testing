# Indicator Spec

Synthesized from `spec/source-spreadsheet.md` (the raw extraction of the 2012
dashboard). This file is the implementation contract for `src/scanner/indicators/`.
Every indicator listed here must be implemented as a pure function and validated per
the strategy at the bottom.

## Function contract

Every indicator is a pure function — OHLCV in, signal out, **no I/O**:

```python
def compute(df: pd.DataFrame, **params) -> dict:
    """
    df: chronologically-ascending bars with columns
        [open, high, low, close, volume], DatetimeIndex.
        (TSC fixture CSVs are newest-first — reverse before passing in.)
    Returns the latest-bar result:
        {
          "signal_value": float,        # the raw indicator value
          "direction": "buy" | "sell" | "neutral",
          ...indicator-specific extras...
        }
    Implementations should also expose a `compute_series(df, **params)`
    returning the full per-bar history — needed by the backtest (Phase E)
    and the v2 LLM context layer.
    """
```

Indicators must not fetch data, read files, or call APIs. All parameters have
defaults; nothing is hardcoded inside formulas.

## Parameter-source rule

Where `Read me - Indicators` and the `Settings` tab disagree, the resolution is in
the discrepancy table at the bottom of `source-spreadsheet.md`. Defaults below follow
that resolution. Treat every default as provisional — final values are set by the
Phase E backtest.

---

# Trade indicators (8)

Each emits `direction` ∈ {buy, sell, neutral}.

## 1. RSI

Standard Wilder RSI. Long when RSI crosses the lower threshold **from below**; short
when it crosses the upper threshold **from above**. The cross matters — a bar already
sitting below 35 is not a fresh buy; the bar that crossed down-to-up through 35 is.

| Param | Default | Notes |
|-------|---------|-------|
| `rsi_days` | 14 | Wilder smoothing window |
| `buy_cross` | 35 | cross above this from below → buy |
| `sell_cross` | 65 | cross below this from above → sell |
| `exit_level` | 50 | exit level (used by backtest, not the live scan) |

Output extras: `rsi` (current), `rsi_prev` (previous bar).
Fixture: `expected_indicators.csv` columns `rsi`, `rsi_prev`, `rsi_flag`.

## 2. Stochastic Oscillator (with divergence)

Two conditions must hold **simultaneously**:
- Long: K% is below the lower threshold **and** there is a bullish divergence —
  price making lower lows while stochastics make higher lows.
- Short: K% is above the upper threshold **and** there is a bearish divergence —
  price making higher highs while stochastics make lower highs.

Stochastic highs/lows are identified where K% crosses D% (from below = a low pivot,
from above = a high pivot). Divergence is detected by comparing the last two such
pivots in price vs. in the stochastic.

| Param | Default | Notes |
|-------|---------|-------|
| `k_days` | 14 | %K window |
| `d_days` | 5 | %D smoothing |
| `buy_below` | 20 | K% must be below this |
| `sell_above` | 80 | K% must be above this |

This is one of the two **gnarly** indicators — the divergence logic is the hard part.
Implement %K/%D first, then pivot detection, then divergence, then combine.
Fixture: no direct stochastic column in `expected_indicators.csv`; validate %K/%D
numerically against a hand-computed sample, and validate divergence with synthetic
fixtures.

## 3. MAV Breakout

Fires when **four** conditions hold simultaneously (all for the upside case; mirror
for downside):
1. **Narrowing** — band width = (highest of the 3 MAVs − lowest of the 3 MAVs). Its
   percentile over the lookback window is below `narrow_threshold`.
2. **Breakout** — the 21-day LOW-price MAV slope has turned positive (downside: the
   21-day HIGH-price MAV slope has turned negative).
3. **Stochastic** — stochastics have turned positive (downside: negative).
4. **Daily close** — price closed above the top band (downside: below the bottom band).

| Param | Default | Notes |
|-------|---------|-------|
| `mav1` | 21 | |
| `mav2` | 34 | |
| `mav3` | 55 | |
| `narrow_threshold` | 0.4 | band-width percentile must be below this |
| `percentile_window` | 250 | lookback for the band-width percentile |
| `k_window` | 14 | stochastic K window for condition 3 |

The second **gnarly** indicator. None of the four conditions is used for exiting.
Output extras: `narrow_pct`, `breakout_flag` (−1/0/+1), `days_since_breakout`.
Fixture: `expected_indicators.csv` columns `mav_narrow_pct`, `mav_breakout_flag`,
`mav_days_since`.

## 4. Daily Trend — Divergence

Percentage change (slope) of the current MAV of the series. Long when the slope
crosses **above** the positive threshold; short when it crosses **below** the
negative threshold. A trend-following signal.

| Param | Default | Notes |
|-------|---------|-------|
| `ma_window` | 21 | |
| `buy_cross` | 0.005 | daily slope threshold; hourly 0.00125, weekly 0.015 |
| `sell_cross` | -0.005 | |
| `exit_level` | 0.0 | |
| `price_series` | `"close"` | also computable on `high` / `low` |

Fixture: `expected_indicators.csv` columns `daily_trend`, `daily_trend_prev`,
`dt_flag`.

## 5. Daily Trend — Contrarian

Same slope computation as #4, inverted logic: a short is taken when the slope crosses
**below** the threshold (the trend was strong for a long time and is now declining).

| Param | Default | Notes |
|-------|---------|-------|
| `ma_window` | 21 | |
| `buy_cross` | -0.005 | |
| `sell_cross` | 0.005 | |
| `exit_level` | 0.0 | |

Share the slope computation with #4 — don't duplicate it.

## 6. Bollinger Band — Normal

Z-score of current price vs. its moving average: `z = (price − MA) / σ`. Long when z
rises above the upper threshold; short when z falls below the lower threshold.

| Param | Default | Notes |
|-------|---------|-------|
| `z_days` | 21 | window for MA and σ |
| `sd_threshold` | 1.5 | long when z > +1.5, short when z < −1.5 |
| `contrarian_threshold` | 0.25 | used by #7 |
| `breakout_history` | 30 | lookback for "days since breakout" |

Output extras: `bollinger_z`, `days_since_breakout`.
Fixture: `expected_indicators.csv` columns `bollinger_z`, `bollinger_days`,
`bollinger_time`.

## 7. Bollinger Band — Contrarian

Same z-score as #6. A new threshold = `sd_threshold − contrarian_threshold` (default
1.5 − 0.25 = 1.25) generates contrarian signals: long when z falls **below** this new
threshold, short when z rises **above** it. Share the z-score computation with #6.

## 8. Congestion / Box Breakout

Detects extended periods of price **congestion** (a tight horizontal range — "the box")
followed by a **breakout** out of that range. This is a classic trading-range /
rectangle / Darvas-style setup.

It is **complementary to MAV Breakout**, not a duplicate. MAV Breakout uses
moving-average *band* compression as the proxy for consolidation. Box Breakout uses
the literal horizontal *price* range. They fire on different setups: a market can
have compressed moving averages without a clean horizontal box, and vice versa.

This indicator is pure OHLCV math. No chart images, no computer vision, no new data
source — the same daily bars every other indicator consumes.

### Definitions

- **Congestion zone (box):** a run of consecutive bars whose combined high/low range
  stays within a variance tolerance. Formally, for bars `[s..e]`:
  `box_high = max(high[s..e])`, `box_low = min(low[s..e])`, and the run is congested
  while `tightness(box_high, box_low) <= max_range`.
- **Valid box:** a congestion zone whose length `(e - s + 1) >= min_congestion_bars`.
  Short tight ranges are noise; only extended ones count.
- **Breakout:** the bar that ends a valid box by closing beyond the box edge by at
  least `breakout_buffer`. Closing up and out → bullish (+1); down and out → bearish (−1).
  A bar that pokes outside intrabar but closes back inside the box is a *false poke* —
  no signal, the box simply continues or ends without a breakout.

### Tightness metric

Two supported, controlled by `range_metric`:

- `"pct"` (default): `(box_high - box_low) / ((box_high + box_low) / 2)` — range as a
  fraction of midprice. Simple, intuitive.
- `"atr"`: `(box_high - box_low) / ATR(atr_window)` — range in ATR multiples. More
  robust across volatility regimes and price levels; prefer this once the engine is
  stable.

### Parameters

| Param                  | Default | Meaning |
|------------------------|---------|---------|
| `min_congestion_bars`  | 15      | Minimum length for a congestion zone to count as a valid box. |
| `max_range`            | 0.06    | Max tightness for the box. With `range_metric="pct"`, 0.06 = 6% of midprice. With `"atr"`, interpret as ATR multiples (e.g. 3.0). |
| `range_metric`         | `"pct"` | `"pct"` or `"atr"`. |
| `atr_window`           | 14      | ATR lookback, only used when `range_metric="atr"`. |
| `breakout_buffer`      | 0.25    | Close must clear the box edge by this much to count as a breakout. Same unit as `range_metric` (fraction of midprice, or ATR multiples). Filters false pokes. |
| `breakout_recency`     | 3       | A breakout stays "fresh" for this many bars. A scan running today still flags a breakout that happened up to N bars ago. Mirrors the spreadsheet's MAV "days since" logic. |

All defaults are starting points — tune in Phase E (backtest), not by eye.

### Detection algorithm

Single forward pass over the chronologically-ordered bars:

```
state: current_run_start = 0
       run_high = high[0], run_low = low[0]

for i in 1 .. n-1:
    candidate_high = max(run_high, high[i])
    candidate_low  = min(run_low,  low[i])

    if tightness(candidate_high, candidate_low) <= max_range:
        # bar i extends the current congestion run
        run_high, run_low = candidate_high, candidate_low
        continue

    # bar i did NOT fit — the run [current_run_start .. i-1] has ended
    run_len = i - current_run_start
    if run_len >= min_congestion_bars:
        # valid box just ended; is bar i a breakout?
        if close[i] > run_high + buffer_abs:   breakout at i = +1
        elif close[i] < run_low  - buffer_abs: breakout at i = -1
        else:                                  no breakout (run ended quietly)
        record box: {start, end=i-1, high=run_high, low=run_low, breakout_dir}

    # start a fresh run at bar i
    current_run_start = i
    run_high, run_low = high[i], low[i]
```

`buffer_abs` is `breakout_buffer` converted to absolute price terms (multiply by
midprice for `"pct"`, by ATR for `"atr"`).

**Output series.** For every bar, the indicator emits:
- `box_active` (bool) — is this bar inside a valid in-progress congestion zone
- `box_high`, `box_low` (float | nan) — edges of the most recent valid box
- `breakout_dir` (−1 / 0 / +1) — breakout on this bar
- `days_since_breakout` (int) — bars since the last breakout

### Output contract (latest bar)

Conforms to the standard indicator contract:

```python
{
    "signal_value": float,   # normalized breakout strength, see below
    "direction": "buy" | "sell" | "neutral",
    "box_high": float | None,
    "box_low": float | None,
    "box_length": int | None,
    "days_since_breakout": int | None,
}
```

`direction` is `buy` if `breakout_dir == +1` within the last `breakout_recency` bars,
`sell` if `−1`, else `neutral`.

`signal_value` is breakout strength normalized to 0–1 for the combo score, consistent
with the other indicators (low = buy, high = sell):
- fresh bullish breakout → `0.25`
- fresh bearish breakout → `0.75`
- no fresh breakout → `0.5`
(Same three-value scheme the spreadsheet uses to normalize Bollinger into the combo.)

### Volume confirmation

Box Breakout is a high-value candidate for volume confirmation: a breakout out of a
long quiet range *on expanding volume* is meaningfully stronger than one on flat
volume. This rides on the existing volume-percentile confirmation logic — volume in a
low percentile **demotes** the signal in the ranking (per the v1 decision: demote, do
not kill). No special handling needed here; the scoring layer applies it uniformly.

### Validation

**This indicator has no ground-truth row in the 2012 spreadsheet.** The sheet's
`expected_indicators.csv` covers RSI, Daily Trend, MAV Breakout, Bollinger, and the
confirmation percentiles — there is no literal box-breakout column. So its tests look
different from every other indicator's:

#### Synthetic fixtures (primary)

Hand-built DataFrames with known answers. Add to `tests/fixtures/synthetic/`:

- **`flat_then_breakout_up.csv`** — 25 bars oscillating inside a 4% range, then 1 bar
  closing 3% above the range high. Expect: `box_length≈25`, `direction="buy"`,
  `days_since_breakout=0` on the last bar.
- **`flat_then_breakout_down.csv`** — mirror of the above, breakout down.
  Expect `direction="sell"`.
- **`false_poke.csv`** — 20 tight bars, then 1 bar whose high pokes above the range
  but whose close lands back inside. Expect: no breakout, `direction="neutral"`.
- **`too_short.csv`** — only 8 bars of tight range then a break. Below
  `min_congestion_bars`. Expect: no valid box, `direction="neutral"`.
- **`trending_no_box.csv`** — a clean uptrend, never congested. Expect:
  `box_active=False` throughout, `direction="neutral"`.
- **`recency_expired.csv`** — a valid breakout `breakout_recency + 2` bars before the
  end. Expect: `direction="neutral"` on the last bar (breakout no longer fresh),
  but `days_since_breakout` correctly set.

#### Eyeball check (secondary)

Run against the 5 real TSC fixtures (WTI/GOLD/EUR/JPY/GBP). No assertions — just
confirm the boxes it finds look sane on a plotted chart. GBP in particular had real
consolidation in the 2012 data; it's the best informal check.

### Notes for implementation

- Operate on chronologically-ascending bars. The TSC fixture CSVs are newest-first —
  reverse before computing (same as every other indicator).
- The detection is O(n) single-pass; performance is a non-issue even at full global
  universe scale.
- Keep the per-bar output series available, not just the latest value — Phase E
  (backtest) and the v2 LLM context layer will both want the box history.

---

# Confirmation indicators (3)

These do **not** emit buy/sell. They confirm, demote, or flag-for-exit a signal
produced by the trade indicators. Output `{percentile: float, state: "confirm" |
"neutral" | "reject"}`.

## 9. MAV Difference Z-Score

Difference between two MAVs; Z-score of that difference over a long history measures
the acceleration of trend direction. A **sign change** in the z-score is a trend
reversal (exit) signal.

| Param | Default |
|-------|---------|
| `mav1` | 20 |
| `mav2` | 50 |
| `z_history` | 180 |

Per the source, this is used only as a backtest exit signal — wire it in but it does
not affect the live ranking in v1.

## 10. Volatility

Realized-volatility percentile over a rolling history. Low percentile = trend
confirmed; high percentile = trend reversal. A trade is **confirmed** when the
percentile is below `confirm_threshold`; flagged for **reversal** when above
`reversal_threshold`.

| Param | Default |
|-------|---------|
| `history` | 180 |
| `confirm_threshold` | 0.3 |
| `reversal_threshold` | 0.7 |

Fixture: `expected_indicators.csv` column `vol_percentile`.

## 11. Volume

Volume percentile over a rolling history. **High** percentile = trend confirmed;
low = reversal. Confirmed when percentile is above `confirm_threshold`; reversal when
below `reversal_threshold`.

| Param | Default |
|-------|---------|
| `history` | 180 |
| `confirm_threshold` | 0.7 |
| `reversal_threshold` | 0.3 |

**v1 ranking decision:** low volume **demotes** a signal in the ranking — it does not
remove it. Volume is an important factor but not a hard gate. See `scoring.md`.
Fixture: `expected_indicators.csv` column `volume_percentile`.

## ~~Open Interest~~ — DROPPED in v1

The original sheet has an OI confirmation indicator (Δ7d OI percentile, 70/30
thresholds). OI is a futures/options field with no single-stock equivalent, so it is
**dropped** for the equities build. The combo simply averages over the remaining
confirmation indicators. Revisit in v2 with a substitute (put/call ratio or short
interest) if wanted. The `oi_percentile` column exists in `expected_indicators.csv`
but is only used to validate the formula on the futures fixtures — it is not part of
the equities pipeline.

---

# Validation strategy

Two classes of test:

**Indicators with a 2012 fixture** (RSI, Daily Trend, MAV Breakout, Bollinger,
Volatility, Volume): feed the full OHLCV history from `tests/fixtures/tsc_2012/
<TICKER>_ohlcv.csv` (reversed to chronological order) into the engine, take the
latest-bar value, and assert it matches the corresponding row in
`expected_indicators.csv`:
- continuous values (rsi, daily_trend, bollinger_z, percentiles): `abs_tol = 1e-3`
- discrete flags (dt_flag, rsi_flag, mav_breakout_flag): exact match
- day counters (mav_days_since, bollinger_days): exact match

**Indicators without a 2012 fixture** (Stochastic divergence logic, Box Breakout):
hand-built synthetic fixtures in `tests/fixtures/synthetic/` with known answers — see
the Box Breakout spec for the six required cases; do the equivalent for stochastic
divergence (a constructed bullish-divergence series, a bearish one, and a no-
divergence control).

Run order for implementation: easy first — RSI, Bollinger (#6/#7), Daily Trend
(#4/#5), Volatility, Volume. Gnarly last — MAV Breakout, Stochastic divergence, Box
Breakout. One indicator per focused Claude Code session.
