# Indicator Spec — Congestion / Box Breakout

> Drop this section into `spec/indicators.md` alongside the other indicators.
> This is trade indicator **#8**. It is the one indicator with no ground-truth
> fixture in the 2012 spreadsheet — see "Validation" below.

## Overview

Detects extended periods of price **congestion** (a tight horizontal range — "the box")
followed by a **breakout** out of that range. This is a classic trading-range /
rectangle / Darvas-style setup.

It is **complementary to MAV Breakout**, not a duplicate. MAV Breakout uses
moving-average *band* compression as the proxy for consolidation. Box Breakout uses
the literal horizontal *price* range. They fire on different setups: a market can
have compressed moving averages without a clean horizontal box, and vice versa.

This indicator is pure OHLCV math. No chart images, no computer vision, no new data
source — the same daily bars every other indicator consumes.

## Definitions

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

## Tightness metric

Two supported, controlled by `range_metric`:

- `"pct"` (default): `(box_high - box_low) / ((box_high + box_low) / 2)` — range as a
  fraction of midprice. Simple, intuitive.
- `"atr"`: `(box_high - box_low) / ATR(atr_window)` — range in ATR multiples. More
  robust across volatility regimes and price levels; prefer this once the engine is
  stable.

## Parameters

| Param                  | Default | Meaning |
|------------------------|---------|---------|
| `min_congestion_bars`  | 15      | Minimum length for a congestion zone to count as a valid box. |
| `max_range`            | 0.06    | Max tightness for the box. With `range_metric="pct"`, 0.06 = 6% of midprice. With `"atr"`, interpret as ATR multiples (e.g. 3.0). |
| `range_metric`         | `"pct"` | `"pct"` or `"atr"`. |
| `atr_window`           | 14      | ATR lookback, only used when `range_metric="atr"`. |
| `breakout_buffer`      | 0.25    | Close must clear the box edge by this much to count as a breakout. Same unit as `range_metric` (fraction of midprice, or ATR multiples). Filters false pokes. |
| `breakout_recency`     | 3       | A breakout stays "fresh" for this many bars. A scan running today still flags a breakout that happened up to N bars ago. Mirrors the spreadsheet's MAV "days since" logic. |

All defaults are starting points — tune in Phase E (backtest), not by eye.

## Detection algorithm

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

## Output contract (latest bar)

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

## Volume confirmation

Box Breakout is a high-value candidate for volume confirmation: a breakout out of a
long quiet range *on expanding volume* is meaningfully stronger than one on flat
volume. This rides on the existing volume-percentile confirmation logic — volume in a
low percentile **demotes** the signal in the ranking (per the v1 decision: demote, do
not kill). No special handling needed here; the scoring layer applies it uniformly.

## Validation

**This indicator has no ground-truth row in the 2012 spreadsheet.** The sheet's
`expected_indicators.csv` covers RSI, Daily Trend, MAV Breakout, Bollinger, and the
confirmation percentiles — there is no literal box-breakout column. So its tests look
different from every other indicator's:

### 1. Synthetic fixtures (primary)

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

### 2. Eyeball check (secondary)

Run against the 5 real TSC fixtures (WTI/GOLD/EUR/JPY/GBP). No assertions — just
confirm the boxes it finds look sane on a plotted chart. GBP in particular had real
consolidation in the 2012 data; it's the best informal check.

## Notes for implementation

- Operate on chronologically-ascending bars. The TSC fixture CSVs are newest-first —
  reverse before computing (same as every other indicator).
- The detection is O(n) single-pass; performance is a non-issue even at full global
  universe scale.
- Keep the per-bar output series available, not just the latest value — Phase E
  (backtest) and the v2 LLM context layer will both want the box history.
