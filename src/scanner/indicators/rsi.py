"""RSI — Relative Strength Index (Wilder smoothing).

Spec: spec/indicators.md §1
Contract: CLAUDE.md §Indicator contract

Direction logic:
  buy    — RSI crosses UP   through buy_cross  (prev < buy_cross  and cur >= buy_cross)
  sell   — RSI crosses DOWN through sell_cross (prev > sell_cross and cur <= sell_cross)
  neutral — otherwise
"""

from __future__ import annotations

import numpy as np
import pandas as pd

NAME = "rsi"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _wilder_rsi(close: pd.Series, rsi_days: int = 14) -> pd.Series:
    """Wilder smoothed RSI.

    Seed: simple average of the first `rsi_days` up/down moves.
    Subsequent bars: RMA = (prev * (period-1) + current) / period.
    """
    delta = close.diff()
    gain = delta.clip(lower=0).values
    loss = (-delta).clip(lower=0).values
    n = len(close)

    avg_g = np.full(n, np.nan)
    avg_l = np.full(n, np.nan)

    if n <= rsi_days:
        return pd.Series(np.nan, index=close.index)

    # Seed with SMA of the first `rsi_days` differences (indices 1..rsi_days)
    avg_g[rsi_days] = gain[1 : rsi_days + 1].mean()
    avg_l[rsi_days] = loss[1 : rsi_days + 1].mean()

    for i in range(rsi_days + 1, n):
        avg_g[i] = (avg_g[i - 1] * (rsi_days - 1) + gain[i]) / rsi_days
        avg_l[i] = (avg_l[i - 1] * (rsi_days - 1) + loss[i]) / rsi_days

    with np.errstate(divide="ignore", invalid="ignore"):
        rs = np.where(avg_l == 0, np.inf, avg_g / avg_l)

    rsi_vals = np.where(np.isinf(rs), 100.0, 100.0 - 100.0 / (1.0 + rs))
    rsi_vals[:rsi_days] = np.nan

    return pd.Series(rsi_vals, index=close.index)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_series(
    df: pd.DataFrame,
    *,
    rsi_days: int = 14,
    buy_cross: float = 35.0,
    sell_cross: float = 65.0,
    exit_level: float = 50.0,  # noqa: ARG001 — reserved for backtest
) -> pd.DataFrame:
    """Return a per-bar DataFrame with columns: rsi, rsi_prev, rsi_flag, direction.

    Parameters
    ----------
    df:
        Chronologically-ascending OHLCV DataFrame (DatetimeIndex or date column).
    rsi_days:
        Wilder smoothing window (default 14).
    buy_cross:
        RSI must cross UP through this level to generate a buy (default 35).
    sell_cross:
        RSI must cross DOWN through this level to generate a sell (default 65).
    exit_level:
        Reserved for the Phase-E backtest exit logic; not used by the live scan.
    """
    close = df["close"].astype(float).reset_index(drop=True)
    rsi = _wilder_rsi(close, rsi_days)
    rsi_prev = rsi.shift(1)

    buy_mask = (rsi_prev < buy_cross) & (rsi >= buy_cross)
    sell_mask = (rsi_prev > sell_cross) & (rsi <= sell_cross)

    flag = np.where(buy_mask, 1, np.where(sell_mask, -1, 0)).astype(int)
    direction = np.where(buy_mask, "buy", np.where(sell_mask, "sell", "neutral"))

    return pd.DataFrame(
        {
            "rsi": rsi.values,
            "rsi_prev": rsi_prev.values,
            "rsi_flag": flag,
            "direction": direction,
        }
    )


def compute(
    df: pd.DataFrame,
    *,
    rsi_days: int = 14,
    buy_cross: float = 35.0,
    sell_cross: float = 65.0,
    exit_level: float = 50.0,
) -> dict:
    """Return the latest-bar RSI result.

    Parameters
    ----------
    df:
        Chronologically-ascending OHLCV DataFrame.

    Returns
    -------
    dict with keys: signal_value, direction, rsi, rsi_prev, rsi_flag.
    """
    series = compute_series(
        df,
        rsi_days=rsi_days,
        buy_cross=buy_cross,
        sell_cross=sell_cross,
        exit_level=exit_level,
    )
    last = series.iloc[-1]
    return {
        "signal_value": float(last["rsi"]),
        "direction": str(last["direction"]),
        "rsi": float(last["rsi"]),
        "rsi_prev": float(last["rsi_prev"]),
        "rsi_flag": int(last["rsi_flag"]),
    }
