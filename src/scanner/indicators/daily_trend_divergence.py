"""Daily Trend — Divergence (trend-following).

Spec: spec/indicators.md §4

The slope is the percentage change of the rolling MA: (MA[t] − MA[t−1]) / MA[t].

Direction logic (cross, not level — a bar already past the threshold is not a
fresh signal):
  buy    — slope crosses UP   above +buy_cross  (prev < buy_cross  and cur >= buy_cross)
  sell   — slope crosses DOWN below  sell_cross (prev > sell_cross and cur <= sell_cross)
  neutral — otherwise

Shares slope computation with daily_trend_contrarian via _daily_trend_core.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from scanner.indicators._daily_trend_core import ma_slope_series

NAME = "daily_trend_divergence"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_series(
    df: pd.DataFrame,
    *,
    ma_window: int = 21,
    buy_cross: float = 0.005,
    sell_cross: float = -0.005,
    exit_level: float = 0.0,  # noqa: ARG001 — reserved for backtest
    price_series: str = "close",
) -> pd.DataFrame:
    """Return a per-bar DataFrame with columns: daily_trend, daily_trend_prev, dt_flag, direction.

    Parameters
    ----------
    df:
        Chronologically-ascending OHLCV DataFrame.
    ma_window:
        Simple MA window (default 21).
    buy_cross:
        Slope must cross UP through this value to generate a buy (default +0.005).
    sell_cross:
        Slope must cross DOWN through this value to generate a sell (default −0.005).
    exit_level:
        Reserved for the Phase-E backtest exit logic; not used by the live scan.
    price_series:
        OHLCV column to use for MA computation (default "close").
    """
    slope = ma_slope_series(df, ma_window=ma_window, price_series=price_series)
    slope_prev = slope.shift(1)

    buy_mask  = (slope_prev < buy_cross)  & (slope >= buy_cross)
    sell_mask = (slope_prev > sell_cross) & (slope <= sell_cross)

    flag = np.where(buy_mask, 1, np.where(sell_mask, -1, 0)).astype(int)
    direction = np.where(buy_mask, "buy", np.where(sell_mask, "sell", "neutral"))

    return pd.DataFrame(
        {
            "daily_trend": slope.values,
            "daily_trend_prev": slope_prev.values,
            "dt_flag": flag,
            "direction": direction,
        }
    )


def compute(
    df: pd.DataFrame,
    *,
    ma_window: int = 21,
    buy_cross: float = 0.005,
    sell_cross: float = -0.005,
    exit_level: float = 0.0,
    price_series: str = "close",
) -> dict:
    """Return the latest-bar Daily Trend Divergence result.

    Returns
    -------
    dict with keys: signal_value, direction, daily_trend, daily_trend_prev, dt_flag.
    """
    series = compute_series(
        df,
        ma_window=ma_window,
        buy_cross=buy_cross,
        sell_cross=sell_cross,
        exit_level=exit_level,
        price_series=price_series,
    )
    last = series.iloc[-1]
    return {
        "signal_value": float(last["daily_trend"]),
        "direction": str(last["direction"]),
        "daily_trend": float(last["daily_trend"]),
        "daily_trend_prev": float(last["daily_trend_prev"]),
        "dt_flag": int(last["dt_flag"]),
    }
