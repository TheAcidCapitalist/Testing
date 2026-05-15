"""Stochastic Oscillator with divergence (trade indicator).

Spec: spec/indicators.md §2

Two conditions must hold simultaneously:
  buy:  K% < buy_below (20) AND bullish divergence
  sell: K% > sell_above (80) AND bearish divergence

Divergence is detected by comparing the last two same-type pivot bars:
  Bullish — price making lower lows (bar's low field) while stochastics
             make higher lows (K at the pivot bar).
  Bearish — price making higher highs (bar's high field) while stochastics
             make lower highs (K at the pivot bar).

Pivots are where K% crosses D%:
  Low pivot  — K crosses D from below (K[t-1] < D[t-1] and K[t] >= D[t])
  High pivot — K crosses D from above (K[t-1] > D[t-1] and K[t] <= D[t])

Implementation choices (documented here, not "corrected"):
  1. Price at a pivot: bar's own `low` field (for bullish) / `high` field (for
     bearish). Not the rolling window minimum/maximum — the literal bar-level
     field, which reflects where price actually touched at that pivot moment.
  2. "Last two pivots": the two most recent pivots of the SAME type (two low
     pivots for bullish, two high pivots for bearish). Not the most recent low
     pivot and the most recent high pivot.
  3. Threshold evaluated at the CURRENT BAR (present tense: "K is below"). The
     spec says the threshold and divergence must hold simultaneously on the bar
     being scanned, not at the pivot bars.

Parameters default to the Settings tab (buy_below=20, sell_above=80) per the
discrepancy table in spec/source-spreadsheet.md — overriding Read-me's 35/65.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from scanner.indicators._stochastic_core import stochastic_d, stochastic_k

NAME = "stochastic"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _find_pivots(k: pd.Series, d: pd.Series, pivot_type: str) -> list[int]:
    """Return bar indices of all low or high pivots.

    Low pivot:  K[i-1] < D[i-1] AND K[i] >= D[i]  (K crosses D from below)
    High pivot: K[i-1] > D[i-1] AND K[i] <= D[i]  (K crosses D from above)
    """
    pivots: list[int] = []
    for i in range(1, len(k)):
        ki, di = k.iloc[i], d.iloc[i]
        kp, dp = k.iloc[i - 1], d.iloc[i - 1]
        if any(np.isnan(v) for v in [ki, di, kp, dp]):
            continue
        if pivot_type == "low" and kp < dp and ki >= di:
            pivots.append(i)
        elif pivot_type == "high" and kp > dp and ki <= di:
            pivots.append(i)
    return pivots


def _bullish_divergence(
    pivots: list[int],
    k: pd.Series,
    df: pd.DataFrame,
) -> bool:
    """True if the last two low pivots show bullish divergence.

    Bullish: price (bar's low) making a lower low while stochastics make a
    higher low.
    """
    if len(pivots) < 2:
        return False
    p1, p2 = pivots[-2], pivots[-1]
    price_low_1 = float(df["low"].iloc[p1])
    price_low_2 = float(df["low"].iloc[p2])
    k1 = float(k.iloc[p1])
    k2 = float(k.iloc[p2])
    return price_low_2 < price_low_1 and k2 > k1


def _bearish_divergence(
    pivots: list[int],
    k: pd.Series,
    df: pd.DataFrame,
) -> bool:
    """True if the last two high pivots show bearish divergence.

    Bearish: price (bar's high) making a higher high while stochastics make a
    lower high.
    """
    if len(pivots) < 2:
        return False
    p1, p2 = pivots[-2], pivots[-1]
    price_high_1 = float(df["high"].iloc[p1])
    price_high_2 = float(df["high"].iloc[p2])
    k1 = float(k.iloc[p1])
    k2 = float(k.iloc[p2])
    return price_high_2 > price_high_1 and k2 < k1


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_series(
    df: pd.DataFrame,
    *,
    k_days: int = 14,
    d_days: int = 5,
    buy_below: float = 20.0,
    sell_above: float = 80.0,
) -> pd.DataFrame:
    """Return a per-bar DataFrame: stoch_k, stoch_d, direction, signal_value.

    Parameters
    ----------
    df:
        Chronologically-ascending OHLCV DataFrame.
    k_days:
        %K lookback window (default 14).
    d_days:
        %D smoothing window (default 5).
    buy_below:
        K must be below this for a buy signal (default 20).
    sell_above:
        K must be above this for a sell signal (default 80).

    Notes
    -----
    Divergence detection requires scanning the ENTIRE prior bar history to find
    pivot bars. The output at each bar therefore depends on all previous bars,
    not just a fixed rolling window — compute_series is O(n²) in the worst
    case (each bar re-scans pivots). For the expected history sizes (hundreds
    of bars) this is not a performance concern.
    """
    df = df.reset_index(drop=True)
    k = stochastic_k(df, k_days=k_days)
    d = stochastic_d(k, d_days=d_days)

    directions: list[str] = []
    signal_values: list[float] = []

    for i in range(len(df)):
        ki = k.iloc[i]
        if np.isnan(ki):
            directions.append("neutral")
            signal_values.append(0.5)
            continue

        k_slice = k.iloc[: i + 1]
        d_slice = d.iloc[: i + 1]

        low_pvts  = _find_pivots(k_slice, d_slice, "low")
        high_pvts = _find_pivots(k_slice, d_slice, "high")

        df_slice = df.iloc[: i + 1]

        if ki < buy_below and _bullish_divergence(low_pvts, k_slice, df_slice):
            directions.append("buy")
            signal_values.append(float(ki) / 100.0)  # low K → low signal_value
        elif ki > sell_above and _bearish_divergence(high_pvts, k_slice, df_slice):
            directions.append("sell")
            signal_values.append(float(ki) / 100.0)  # high K → high signal_value
        else:
            directions.append("neutral")
            signal_values.append(0.5)

    return pd.DataFrame({
        "stoch_k": k.values,
        "stoch_d": d.values,
        "direction": directions,
        "signal_value": signal_values,
    })


def compute(
    df: pd.DataFrame,
    *,
    k_days: int = 14,
    d_days: int = 5,
    buy_below: float = 20.0,
    sell_above: float = 80.0,
) -> dict:
    """Return the latest-bar stochastic divergence result.

    Returns
    -------
    dict with keys:
        signal_value (float 0-1), direction ("buy"|"sell"|"neutral"),
        stoch_k (float), stoch_d (float).
    """
    df = df.reset_index(drop=True)
    k = stochastic_k(df, k_days=k_days)
    d = stochastic_d(k, d_days=d_days)

    ki = float(k.iloc[-1])
    di = float(d.iloc[-1])

    low_pvts  = _find_pivots(k, d, "low")
    high_pvts = _find_pivots(k, d, "high")

    if not np.isnan(ki) and ki < buy_below and _bullish_divergence(low_pvts, k, df):
        direction = "buy"
        signal_value = ki / 100.0
    elif not np.isnan(ki) and ki > sell_above and _bearish_divergence(high_pvts, k, df):
        direction = "sell"
        signal_value = ki / 100.0
    else:
        direction = "neutral"
        signal_value = 0.5

    return {
        "signal_value": signal_value,
        "direction": direction,
        "stoch_k": ki,
        "stoch_d": di,
    }
