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

Detects extended periods of price **congestion** (a tight horizontal range) followed
by a **breakout** out of that range. The longer and tighter the base, the larger the
typical subsequent move. This is a classic trading-range / rectangle / Darvas-style
setup, generalized to work at any bar resolution.

**Architectural note — single-timeframe indicator.** This indicator is
resolution-agnostic: it accepts whatever chronologically-ordered bar series it
receives (daily, weekly, monthly, or quarterly) and applies the same logic. The
orchestrator (Phase C addendum) handles resampling daily OHLCV into coarser
resolutions; multi-timeframe alignment (ranking stocks that fire on multiple
resolutions simultaneously) is handled in the scoring layer. Do not put resampling
logic or resolution-awareness inside this function. See `spec/box-breakout-mt.md`.

It is **complementary to MAV Breakout**, not a duplicate. MAV Breakout uses
moving-average *band* compression as the proxy for consolidation. Box Breakout uses
the literal horizontal *price* range and a rolling-maximum resistance ceiling. They
fire on different setups.

This indicator is pure OHLCV math — no chart images, no computer vision, no new data
source beyond the standard OHLCV bars every other indicator consumes.

### Two parallel detection signals

The brief describes two detection signals that compose the congestion diagnosis:

### Congestion Predicate (The Core Algorithm)

A fixed lookback window of length `lookback` is analyzed for each bar `i`.
The window `W` spans from `i - lookback` to `i - 1`.

Within window `W`:
- `box_high` = maximum `high` over `W`.
- `box_low` = minimum `low` over `W`.

A single bar `j` within `W` is **bullish-congested** if it satisfies BOTH:
1. **Resistance-zone proximity**: `close[j] >= box_high * (1 - touch_tolerance)`
2. **Volatility compression**: `ATR(atr_window)[j] / ATR(atr_long_window)[j] <= compression_threshold`

A single bar `j` within `W` is **bearish-congested** if it satisfies BOTH:
1. **Support-zone proximity**: `close[j] <= box_low * (1 + touch_tolerance)`
2. **Volatility compression**: `ATR(atr_window)[j] / ATR(atr_long_window)[j] <= compression_threshold`

For the current bar `i`:
- The setup has a **valid bullish box** if the count of bullish-congested bars in `W` is `>= lookback * duration_pct`.
- The setup has a **valid bearish box** if the count of bearish-congested bars in `W` is `>= lookback * duration_pct`.

### Breakout Trigger

If a valid box exists, we check bar `i` for a breakout:
- **Bullish breakout (buy)**: Valid bullish box AND `close[i] > box_high * (1 + breakout_buffer)`.
- **Bearish breakdown (sell)**: Valid bearish box AND `close[i] < box_low * (1 - breakout_buffer)`.

**Volume expansion (conditional extra):**
Volume is not gated. It stays a separate confirmation indicator. However, the indicator exposes a `volume_expansion` boolean extra:
`volume_expansion = volume[i] >= vol_mult * rolling_mean(volume, vol_window)[i-1]`.

### Parameters

| Param | Default | Meaning |
|-------|---------|---------|
| `lookback` | 60 | Fixed lookback window length. |
| `duration_pct` | 0.75 | Fraction of `lookback` that must be congested (e.g. 0.75 = 75%). |
| `touch_tolerance` | 0.05 | Proximity band for Signal A (e.g. 0.05 = 5%). |
| `compression_threshold` | 0.8 | Threshold for ATR ratio. |
| `atr_window` | 14 | Short ATR lookback. |
| `atr_long_window` | 50 | Longer ATR baseline. |
| `breakout_buffer` | 0.0 | Additional price clearance beyond the box edge (percentage). |
| `vol_mult` | 1.5 | Volume must be ≥ this multiple of trailing average to set `volume_expansion=True`. |
| `vol_window` | 20 | Trailing volume average window for `vol_mult`. |
| `breakout_recency` | 3 | A breakout stays "fresh" for this many bars. |
| `mode` | `"confirmed"` | Operate on completed bars. |

### Output contract (latest bar)

Unchanged from v1 — the enhanced trigger conditions add to the *qualification* of the
breakout, not to the output shape:

```python
{
    "signal_value": float,              # 0.25 buy, 0.75 sell, 0.5 neutral
    "direction": "buy" | "sell" | "neutral",
    "box_high": float | None,
    "box_low": float | None,
    "box_length": int | None,           # bars in the congestion zone
    "days_since_breakout": int | None,
}
```

`signal_value` uses the three-value scheme (low = buy, consistent with all other
breakout-type indicators). `direction` is `buy` if `breakout_dir == +1` within the
last `breakout_recency` bars, `sell` if `−1`, else `neutral`.

### Validation

**No ground-truth row in the 2012 spreadsheet.** The synthetic fixtures remain
the primary validation; all existing tests remain valid against the v1 parameter set.
Additional synthetic fixtures required for the Phase B addendum (enhanced-spec tests):

- **`pct_duration_threshold.csv`**
- **`vol_expansion_trigger.csv`**
- **`trend_filter_gated.csv`**

#### Eyeball check (secondary)

Run against the 5 real TSC fixtures (WTI/GOLD/EUR/JPY/GBP). No assertions — confirm
the boxes found look sane. GBP had real consolidation in the 2012 data.

### Notes for implementation

- Operate on chronologically-ascending bars. The TSC fixture CSVs are newest-first —
  reverse before computing (same as every other indicator).
- The detection is O(n) single-pass; performance is a non-issue at full global scale.
- Keep the per-bar output series available (`compute_series`) — Phase E and the v2
  LLM context layer both need box history.
- The indicator receives a pre-resampled series and has no knowledge of wall-clock
  time or bar resolution. Resampling is the orchestrator's responsibility
  (see `spec/multi-timeframe.md`).

---

# Confirmation indicators (3)

These do **not** emit buy/sell. They confirm, demote, or flag-for-exit a signal
produced by the trade indicators.

**Default output shape (Volatility, Volume):** `{percentile: float, state: "confirm" |
"neutral" | "reject"}`.

**Exception — MAV Difference Z-Score (#9):** Uses a z-score and a sign-change
flag, not a percentile rank. Output: `{mav_diff: float|None, z_score: float|None,
reversal: bool, mav1_value: float|None, mav2_value: float|None}`. See §9 for details.

## 9. MAV Difference Z-Score

Difference between two MAVs; Z-score of that difference over a long history measures
the acceleration of trend direction. A **sign change** in the z-score is a trend
reversal (exit) signal.

**MA type:** Simple moving average (SMA). "MAV" throughout the spreadsheet always
means SMA (same as Daily Trend and MAV Breakout). "Exponential" in the source
description refers to the kind of move measured, not the MA type.

**Output shape (exception to the {percentile, state} default):**
`{mav_diff: float|None, z_score: float|None, reversal: bool, mav1_value: float|None,
mav2_value: float|None}`. See the confirmation-indicators preamble above.

**Zero-touch rule:** Exactly-zero z is "no sign". Reversal fires on the first bar
with a clearly non-zero sign opposite to the most recent non-zero sign. Sign memory
persists through zero bars (and NaN warmup bars). Example: z = +0.5 → 0.0 → −0.3:
no reversal at z=0; reversal fires at z=−0.3.

**v1 role:** Exit signal for the Phase E backtest only. Does NOT feed the live
combo score or ranking in v1.

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
