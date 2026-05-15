"""MAV Breakout — trade indicator.

Spec: spec/indicators.md §3

Fires when four conditions hold simultaneously (upside case; downside mirrors):
  1. Narrowing   — band width percentile < narrow_threshold.
                   band_width = max(SMA1, SMA2, SMA3) − min(SMA1, SMA2, SMA3).
  2. Slope turn  — 21-day LOW-price SMA slope turns positive
                   (prev_slope ≤ 0 AND curr_slope > 0).
                   Downside: 21-day HIGH-price SMA slope turns negative.
  3. Stochastic  — %K day-to-day diff turns positive
                   (prev_diff ≤ 0 AND curr_diff > 0).
                   Downside: turns negative.
  4. Close       — close above top band (max of 3 SMAs).
                   Downside: close below bottom band.

Interpretation calls documented here:
  • "MAV" throughout = SMA (simple moving average), consistent with every other
    indicator in the spreadsheet.
  • Condition 2 uses LOW-price SMA for upside, HIGH-price SMA for downside,
    both with the mav1 (21-bar) window.
  • "Turned positive/negative" = one-bar transition, not a level check.
  • "Stochastics turned positive" = %K first-diff turned positive (parallel to
    condition 2's slope transition).
  • "Top band" = max of the three close SMAs; "bottom band" = min.

Output contract:
  compute() returns:
    {
        "signal_value":       float,          # 0.25 buy | 0.75 sell | 0.50 neutral
        "direction":          str,            # "buy" | "sell" | "neutral"
        "narrow_pct":         float | None,   # raw percentile of band width
        "breakout_flag":      int,            # signed bars since last signal
                                             #   (+N: buy N bars ago, −N: sell N bars ago, 0: none)
                                             #   (0 on the signal bar itself)
        "days_since_breakout": int,           # consecutive bars narrow_pct < threshold
                                             #   (0 on first narrow bar, 1 on second, …)
    }

Fixture encoding (expected_indicators.csv):
  mav_narrow_pct    → narrow_pct
  mav_breakout_flag → breakout_flag (signed days since last signal)
  mav_days_since    → days_since_breakout (consecutive narrow-band bars, 0-indexed)

Warmup: mav3 bars for the slowest SMA + 2 bars for the percentile min_periods.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from scanner.indicators._percentile import percentile_rank
from scanner.indicators._stochastic_core import stochastic_k

NAME = "mav_breakout"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_series(
    df: pd.DataFrame,
    *,
    mav1: int = 21,
    mav2: int = 34,
    mav3: int = 55,
    narrow_threshold: float = 0.4,
    percentile_window: int = 250,
    k_window: int = 14,
) -> pd.DataFrame:
    """Return a per-bar DataFrame with all intermediate and output columns.

    Parameters
    ----------
    df:
        Chronologically-ascending OHLCV DataFrame.
    mav1, mav2, mav3:
        SMA windows (default 21, 34, 55).
    narrow_threshold:
        Condition 1 threshold: narrow_pct must be strictly below this.
    percentile_window:
        Rolling window for the band-width percentile (default 250).
    k_window:
        Stochastic %K lookback window for condition 3 (default 14).
    """
    df = df.reset_index(drop=True)
    n = len(df)
    close = df["close"].astype(float)
    low = df["low"].astype(float)
    high = df["high"].astype(float)

    # -----------------------------------------------------------------------
    # Three close SMAs and band geometry
    # -----------------------------------------------------------------------
    ma1 = close.rolling(mav1).mean()
    ma2 = close.rolling(mav2).mean()
    ma3 = close.rolling(mav3).mean()

    ma_df = pd.concat([ma1, ma2, ma3], axis=1)
    band_high = ma_df.max(axis=1, skipna=False)
    band_low_ma = ma_df.min(axis=1, skipna=False)
    band_width = band_high - band_low_ma

    # -----------------------------------------------------------------------
    # Condition 1: band-width percentile
    # -----------------------------------------------------------------------
    narrow_pct = percentile_rank(band_width, percentile_window)

    # -----------------------------------------------------------------------
    # Condition 2: slope of mav1-window LOW / HIGH SMA turned
    # -----------------------------------------------------------------------
    low_ma = low.rolling(mav1).mean()
    low_slope = (low_ma - low_ma.shift(1)) / low_ma
    high_ma = high.rolling(mav1).mean()
    high_slope = (high_ma - high_ma.shift(1)) / high_ma

    cond2_up = (low_slope.shift(1) <= 0) & (low_slope > 0)
    cond2_dn = (high_slope.shift(1) >= 0) & (high_slope < 0)

    # -----------------------------------------------------------------------
    # Condition 3: %K first-difference turned
    # -----------------------------------------------------------------------
    k = stochastic_k(df, k_days=k_window)
    k_diff = k - k.shift(1)
    cond3_up = (k_diff.shift(1) <= 0) & (k_diff > 0)
    cond3_dn = (k_diff.shift(1) >= 0) & (k_diff < 0)

    # -----------------------------------------------------------------------
    # Condition 4: close vs band
    # -----------------------------------------------------------------------
    cond4_up = close > band_high
    cond4_dn = close < band_low_ma

    # -----------------------------------------------------------------------
    # Condition 1 boolean
    # -----------------------------------------------------------------------
    cond1 = narrow_pct < narrow_threshold

    # Combined upside / downside events (all 4 simultaneously)
    upside = cond1 & cond2_up & cond3_up & cond4_up
    downside = cond1 & cond2_dn & cond3_dn & cond4_dn

    # -----------------------------------------------------------------------
    # Forward pass: breakout_flag, days_since_breakout, direction
    # -----------------------------------------------------------------------
    np_vals = narrow_pct.values
    up_arr = upside.values
    dn_arr = downside.values

    breakout_flag_arr = np.zeros(n, dtype=float)
    narrow_days_arr = np.zeros(n, dtype=int)
    direction_arr: list[str] = ["neutral"] * n
    signal_value_arr: list[float] = [0.5] * n

    last_dir: int = 0       # +1 buy, -1 sell, 0 never
    last_signal_i: int = -1
    narrow_streak: int = 0

    for i in range(n):
        # --- consecutive narrow bars (0-indexed from first narrow bar) ---
        val = np_vals[i]
        if not np.isnan(val) and val < narrow_threshold:
            narrow_streak += 1
        else:
            narrow_streak = 0
        narrow_days_arr[i] = max(0, narrow_streak - 1)

        # --- signal event ---
        if up_arr[i]:
            last_dir = 1
            last_signal_i = i
        elif dn_arr[i]:
            last_dir = -1
            last_signal_i = i

        # --- breakout_flag: signed bars since last signal (0 on signal bar) ---
        if last_signal_i >= 0:
            breakout_flag_arr[i] = last_dir * (i - last_signal_i)
        # else stays 0

        # --- direction and signal_value persist from last signal ---
        if last_dir == 1:
            direction_arr[i] = "buy"
            signal_value_arr[i] = 0.25
        elif last_dir == -1:
            direction_arr[i] = "sell"
            signal_value_arr[i] = 0.75
        # else neutral / 0.5

    return pd.DataFrame({
        "mav1_value":    ma1.values,
        "mav2_value":    ma2.values,
        "mav3_value":    ma3.values,
        "band_high":     band_high.values,
        "band_low":      band_low_ma.values,
        "band_width":    band_width.values,
        "narrow_pct":    narrow_pct.values,
        "narrow_days":   narrow_days_arr,
        "breakout_flag": breakout_flag_arr,
        "direction":     direction_arr,
        "signal_value":  signal_value_arr,
    })


def compute(
    df: pd.DataFrame,
    *,
    mav1: int = 21,
    mav2: int = 34,
    mav3: int = 55,
    narrow_threshold: float = 0.4,
    percentile_window: int = 250,
    k_window: int = 14,
) -> dict:
    """Return the latest-bar MAV Breakout result.

    Returns
    -------
    dict with keys: signal_value, direction, narrow_pct (float|None),
    breakout_flag (int), days_since_breakout (int).
    """
    series = compute_series(
        df,
        mav1=mav1, mav2=mav2, mav3=mav3,
        narrow_threshold=narrow_threshold,
        percentile_window=percentile_window,
        k_window=k_window,
    )
    last = series.iloc[-1]

    np_val = last["narrow_pct"]
    narrow_pct_out = None if np.isnan(np_val) else float(np_val)

    return {
        "signal_value":        float(last["signal_value"]),
        "direction":           str(last["direction"]),
        "narrow_pct":          narrow_pct_out,
        "breakout_flag":       int(last["breakout_flag"]),
        "days_since_breakout": int(last["narrow_days"]),
    }
