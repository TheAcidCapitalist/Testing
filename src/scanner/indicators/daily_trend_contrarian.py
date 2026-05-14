"""Daily Trend — Contrarian (mean-reversion on MA slope).

Spec: spec/indicators.md §5

Same slope as Divergence: (MA[t] − MA[t−1]) / MA[t].  The signal logic is
inverted — a short is taken when a strong upward trend begins to fade, a long
when a downward trend begins to recover.

Direction logic (cross, not level):
  buy    — slope crosses UP   above buy_cross  (default −0.005): trend recovering from negative
  sell   — slope crosses DOWN below sell_cross (default +0.005): trend fading from positive
  neutral — otherwise

Note: these thresholds are different from Divergence's, so the two indicators
fire on different crossing events, not opposite reactions to the same event.

Shares slope computation with daily_trend_divergence via _daily_trend_core.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from scanner.indicators._daily_trend_core import ma_slope_series

NAME = "daily_trend_contrarian"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_series(
    df: pd.DataFrame,
    *,
    ma_window: int = 21,
    buy_cross: float = -0.005,
    sell_cross: float = 0.005,
    exit_level: float = 0.0,  # noqa: ARG001 — reserved for backtest
    price_series: str = "close",
) -> pd.DataFrame:
    """Return a per-bar DataFrame with columns: daily_trend, daily_trend_prev, direction.

    Parameters
    ----------
    df:
        Chronologically-ascending OHLCV DataFrame.
    ma_window:
        Simple MA window (default 21).
    buy_cross:
        Slope must cross UP through this value to generate a buy (default −0.005).
    sell_cross:
        Slope must cross DOWN through this value to generate a sell (default +0.005).
    exit_level:
        Reserved for the Phase-E backtest exit logic; not used by the live scan.
    price_series:
        OHLCV column to use for MA computation (default "close").
    """
    slope = ma_slope_series(df, ma_window=ma_window, price_series=price_series)
    slope_prev = slope.shift(1)

    buy_mask  = (slope_prev < buy_cross)  & (slope >= buy_cross)
    sell_mask = (slope_prev > sell_cross) & (slope <= sell_cross)

    direction = np.where(buy_mask, "buy", np.where(sell_mask, "sell", "neutral"))

    return pd.DataFrame(
        {
            "daily_trend": slope.values,
            "daily_trend_prev": slope_prev.values,
            "direction": direction,
        }
    )


def compute(
    df: pd.DataFrame,
    *,
    ma_window: int = 21,
    buy_cross: float = -0.005,
    sell_cross: float = 0.005,
    exit_level: float = 0.0,
    price_series: str = "close",
) -> dict:
    """Return the latest-bar Daily Trend Contrarian result.

    Returns
    -------
    dict with keys: signal_value, direction, daily_trend, daily_trend_prev.
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
    }
