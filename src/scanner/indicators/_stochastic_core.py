"""Shared stochastic %K / %D computation.

Private module (leading underscore) — skipped by the registry.

Used by stochastic.py (divergence) and mav_breakout.py (condition 3).

%K = (close − lowest_low_over_k_days) / (highest_high_over_k_days − lowest_low_over_k_days) × 100
%D = simple moving average of %K over d_days

Matches pandas-ta stoch(k=k_days, d=d_days, smooth_k=1) with zero diff on real data.
"""

from __future__ import annotations

import pandas as pd


def stochastic_k(df: pd.DataFrame, k_days: int = 14) -> pd.Series:
    """Return %K series (0–100), chronologically ascending.

    Parameters
    ----------
    df:
        Chronologically-ascending OHLCV DataFrame with columns [high, low, close].
    k_days:
        Lookback window for the highest high / lowest low (default 14).
    """
    low_min = df["low"].astype(float).rolling(k_days).min()
    high_max = df["high"].astype(float).rolling(k_days).max()
    close = df["close"].astype(float)
    rng = high_max - low_min
    # When range is zero (flat market), return 50 to avoid division by zero.
    k = (close - low_min) / rng.where(rng != 0, other=float("nan")) * 100
    return k.reset_index(drop=True)


def stochastic_d(k: pd.Series, d_days: int = 5) -> pd.Series:
    """Return %D series — simple moving average of %K.

    Parameters
    ----------
    k:
        %K series (output of stochastic_k).
    d_days:
        Smoothing window (default 5).
    """
    return k.rolling(d_days).mean()
