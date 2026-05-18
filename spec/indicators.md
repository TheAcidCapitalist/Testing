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
logic or resolution-awareness inside this function. See `spec/multi-timeframe.md`.

It is **complementary to MAV Breakout**, not a duplicate. MAV Breakout uses
moving-average *band* compression as the proxy for consolidation. Box Breakout uses
the literal horizontal *price* range and a rolling-maximum resistance ceiling. They
fire on different setups.

This indicator is pure OHLCV math — no chart images, no computer vision, no new data
source beyond the standard OHLCV bars every other indicator consumes.

### Two parallel detection signals

The brief describes two detection signals that compose the congestion diagnosis:

**Signal A — Resistance-zone proximity:**
The rolling maximum high over the lookback window defines a resistance ceiling. The
stock is considered to be approaching or testing this ceiling when:
```
close >= resistance_zone × (1 − touch_tolerance)
```
`touch_tolerance` scales with timeframe and base age: ~5% for short-term daily
bases, ~10–20% for multi-year bases (long bases are inherently wider).

**Signal B — Volatility compression:**
The price action is tightening, measured by one of:
- **ATR ratio:** `ATR(atr_window) / ATR(atr_long_window)` — a low ratio signals
  compression. Scale-invariant; works on any bar resolution.
- **Bollinger Band Width:** `(upper_band − lower_band) / middle_band`.
- **Range-to-ATR ratio:** `(rolling_high − rolling_low) / ATR(atr_window)`.

Default metric: ATR ratio. The compression threshold is a parameter. All three metrics
are scale-invariant and work identically on daily, weekly, or monthly bars.

**⚠ Open question #2 — AND vs OR:**
The brief lists both signals without specifying their logical relationship. Options:
- **(A) AND:** Both resistance-zone proximity AND volatility compression must be active
  for the congestion zone to qualify. More precise; may miss wide-but-persistent bases
  where Signal A does not fire until very late in the consolidation.
- **(B) OR:** Either signal is sufficient. More permissive; risks firing on trending
  markets approaching a prior resistance level without genuine compression.
- **(C) Hybrid:** Both required on the breakout bar; only one required for each bar
  to count toward the minimum-duration percentage.

**Resolution pending user decision.** The v1 implementation uses Signal A (tightness
test) only.

### Minimum duration

Congestion must persist for at least `min_congestion_pct` of the lookback window
(default: `0.75` — the midpoint of the brief's 70–80% guidance). For a 60-bar
lookback, at least 45 bars must be in congestion; for a 104-week lookback, at least
78 weeks.

**v1 compatibility:** The existing implementation uses `min_congestion_bars` (default
15, absolute). The percentage-based default (`lookback=60, min_congestion_pct=0.75`)
yields a minimum of 45 bars — materially different from 15. Both parameters are
preserved; `min_congestion_pct` governs when `lookback` is set. The v1 tests remain
valid against their original parameter set.

**⚠ Open question #3 — Definition of "true congestion" per bar:**
To count toward the minimum-duration percentage, a bar must be "in congestion." The
brief does not define this per-bar criterion. Options:
- **(A) Tightness test (current v1):** Including bar i does not cause
  `tightness(run_high, run_low)` to exceed `max_range`.
- **(B) Signal B only:** Bar i shows volatility compression (Signal B active on bar i).
- **(C) Both signals active on bar i.**
- **(D) No new range extremes:** Bar i does not set a new high or low outside a
  tolerance band around the running box edges.

**Resolution pending user decision.**

### Parameters

| Param | Default | Meaning |
|-------|---------|---------|
| `lookback` | 60 | Lookback window in bars. Resolution-agnostic: 60 daily bars ≈ 3 months; 60 weekly bars ≈ 14 months. See `spec/multi-timeframe.md` for per-mode recommendations. |
| `min_congestion_pct` | 0.75 | Fraction of `lookback` that must be in congestion (0.75 = 75%). Governs when `lookback` is set; falls back to `min_congestion_bars` otherwise. |
| `min_congestion_bars` | 15 | v1 absolute minimum (preserved for backward compatibility). |
| `max_range` | 0.06 | Maximum tightness for the running box. With `range_metric="pct"`, 0.06 = 6% of midprice. With `"atr"`, ATR multiples (e.g. 3.0). |
| `range_metric` | `"pct"` | `"pct"` or `"atr"`. ATR-based is more robust across volatility regimes and price levels. |
| `atr_window` | 14 | Short ATR lookback. Used when `range_metric="atr"`, for the ATR-ratio compression metric, and for the ATR-expansion breakout condition. |
| `atr_long_window` | 50 | Longer ATR baseline for the ATR-ratio compression metric and the ATR-expansion condition. |
| `touch_tolerance` | 0.05 | Resistance-zone proximity band for Signal A (0.05 = 5% of rolling max high). |
| `breakout_buffer` | 0.0 | Additional price clearance beyond the resistance level for the breakout trigger. ⚠ See open question #1. Default 0 for v1; tune in Phase E. |
| `vol_expansion_factor` | `None` | Volume must be ≥ this multiple of the trailing average to confirm a breakout. `None` = not checked in trigger. ⚠ See architectural decision below. |
| `vol_window` | 20 | Trailing volume average window for `vol_expansion_factor`. |
| `atr_expansion_factor` | `None` | Current `ATR(atr_window)` must be ≥ this multiple of `ATR(atr_long_window)` to confirm a breakout. `None` = not checked. ⚠ See open question #4. |
| `trend_filter_window` | `None` | Bars for an optional trend-filter MA. `None` = disabled. If set, bullish breakouts only when `close > SMA(trend_filter_window)`; bearish only when `close < SMA`. |
| `confirmed_close` | `True` | If `True`, only act on fully-closed bars. If `False`, flag in-progress breakouts on the forming bar. ⚠ See open question #5. |
| `breakout_recency` | 3 | A breakout stays "fresh" for this many bars. |

All defaults are starting points — tune in Phase E (backtest), not by eye.

### Open question #1 — Resistance zone vs. breakout level

The brief says the resistance zone is the rolling maximum high and the breakout is
"close above the resistance zone." It is ambiguous whether:
- **(A) Breakout = close > max_high** — strict rolling ceiling, no buffer.
- **(B) Breakout = close > max_high + buffer_abs** — price clears the ceiling by an
  additional buffer (the v1 `breakout_buffer` approach; `breakout_buffer=0.0` in the
  enhanced spec makes option A the default).
- **(C) Signal A touch ≠ breakout trigger:** Signal A fires when close is *within*
  tolerance of max_high (approaching resistance); the breakout trigger fires when close
  *clears* max_high by a buffer. These are distinct events.

Option (C) is the most internally consistent with the two-signal design: Signal A is
the "approaching" event; the breakout is the "cleared" event. **Resolution pending
user decision.**

### Open question #4 — ATR expansion threshold

The brief specifies "expansion in ATR" as part of the breakout trigger but does not
define:
- What multiple of a baseline ATR constitutes "expansion" (1.1×? 1.5×? 2×?).
- What the baseline is: `ATR(atr_long_window)` vs. a 52-period ATR?
- Whether this is a hard gate (no breakout signal without ATR expansion) or an
  advisory demotion in ranking.

**Resolution pending user decision.**

### Open question #5 — Confirmed-close vs in-progress

On weekly and monthly bars the current bar may still be forming (a weekly bar captured
on Wednesday; a monthly bar captured on the 10th). Options:
- **(A) Confirmed only (default):** Only act on fully-closed bars. Lags by at most one
  bar period. `confirmed_close=True`.
- **(B) In-progress:** Flag intrabar moves above resistance. Useful for early alerts;
  may produce false positives when the bar closes back inside the zone.
- **(C) Both, flagged:** Compute both; label the signal `"confirmed"` or `"in_progress"`.

**Resolution pending user decision.**

### Breakout trigger

The breakout fires on the bar that exits a valid congestion zone (meeting the minimum
duration requirement). Trigger conditions, in order:

**Price condition (required):**
`close > resistance_level` — where `resistance_level` is defined per open question #1.
The current v1 default uses `resistance_level = run_high` with `breakout_buffer=0.0`.

**ATR expansion (conditional — open question #4):**
If `atr_expansion_factor` is not `None`:
`ATR(atr_window) >= atr_expansion_factor × ATR(atr_long_window)`.

**Volume expansion (conditional — architectural decision below):**
If `vol_expansion_factor` is not `None`:
`volume >= vol_expansion_factor × rolling_mean(volume, vol_window)`.
The brief specifies 1.5–2× the trailing average as the typical range.

**Optional trend filter:**
If `trend_filter_window` is not `None`, bullish breakouts require `close > SMA(close,
trend_filter_window)`; bearish require `close < SMA`. Typical values: 200 bars for
daily, 40 for weekly, 10 for monthly. The indicator does not know its resolution — the
caller passes the appropriate window.

### Architectural decision — volume in trigger vs. separate indicator

**This is the key design tension from the brief and requires user resolution.**

The brief specifies volume ≥ 1.5–2× trailing average as part of the breakout
*trigger* — i.e., no volume expansion means no signal. The current architecture has
Volume as a separate confirmation indicator (#11): it does not gate signals, it only
*demotes* them in the ranking (the explicit v1 decision: "demote, do not remove").

Options:
- **(A) Keep volume separate (current architecture).** `vol_expansion_factor=None` by
  default. A low-volume breakout appears in the output but is demoted in ranking via
  indicator #11. Preserves the clean layer separation. **Recommended for v1.**
- **(B) Integrate volume into the trigger.** Set a non-`None` default for
  `vol_expansion_factor`. A breakout without volume expansion produces
  `direction="neutral"`. Indicator #11 becomes partially redundant for Box Breakout
  signals. Tighter signals, fewer false positives.
- **(C) Soft gate — new direction value.** A low-volume breakout produces
  `direction="buy_unconfirmed"`. The scoring layer treats it differently from a
  confirmed buy. Requires extending the direction enum and updating all downstream
  consumers.

**Recommendation: Option (A) for v1.** Revisit in Phase E if the backtest shows that
requiring volume expansion materially improves signal quality beyond the demotion
mechanism.

### Detection algorithm

Single forward pass over the chronologically-ordered bars (O(n), same structure as v1):

```
min_cong_bars = round(min_congestion_pct × lookback)   # if lookback is set
                OR min_congestion_bars                    # v1 fallback

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
    if run_len >= min_cong_bars:
        # valid congestion zone; is bar i a breakout?
        resistance = run_high                              # ⚠ open question #1
        buffer_abs = convert_buffer(breakout_buffer, midprice, atr)
        if close[i] > resistance + buffer_abs:
            # check optional ATR expansion, volume expansion, trend filter
            breakout_dir = +1  # if all active conditions pass; else 0
        elif close[i] < run_low - buffer_abs:
            breakout_dir = -1  # ditto
        else:
            breakout_dir = 0   # no breakout (run ended quietly)
        record box: {start, end=i-1, high=run_high, low=run_low, breakout_dir}

    # start a fresh run at bar i
    current_run_start = i
    run_high, run_low = high[i], low[i]
```

Signal B (volatility compression) runs in parallel over the same forward pass,
tracking whether each bar in the running box also meets the compression criterion.
Open question #2 (AND vs OR) determines how Signal B gates the congestion
qualification and the breakout decision.

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

**No ground-truth row in the 2012 spreadsheet.** The six v1 synthetic fixtures remain
the primary validation; all existing tests remain valid against the v1 parameter set:

- `flat_then_breakout_up.csv` — bullish breakout after extended congestion
- `flat_then_breakout_down.csv` — bearish mirror
- `false_poke.csv` — intrabar poke, close back inside
- `too_short.csv` — congestion below minimum duration
- `trending_no_box.csv` — no congestion at all
- `recency_expired.csv` — valid breakout but stale (beyond `breakout_recency`)

Additional synthetic fixtures required for the Phase B addendum (enhanced-spec tests):

- **`pct_duration_threshold.csv`** — base spanning exactly `min_congestion_pct` of
  `lookback`; one bar shorter must not qualify.
- **`vol_expansion_trigger.csv`** — breakout bar with and without sufficient volume,
  when `vol_expansion_factor` is set.
- **`trend_filter_gated.csv`** — valid breakout that is suppressed by the trend filter.

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
