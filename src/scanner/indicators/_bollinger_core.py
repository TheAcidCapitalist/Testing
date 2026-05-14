"""Shared Bollinger Band computation used by bollinger_normal and bollinger_contrarian.

This module is private (leading underscore) so the registry skips it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def bollinger_z_series(close: pd.Series, z_days: int = 21) -> pd.Series:
    """Compute the z-score of price vs. its rolling mean.

    z = (price − MA(z_days)) / σ(z_days, ddof=1)

    Uses sample standard deviation (ddof=1) — matches the 2012 fixture values.
    Returns NaN for the first (z_days − 1) bars where the window is not yet full.
    """
    ma = close.rolling(z_days).mean()
    sd = close.rolling(z_days).std(ddof=1)
    return (close - ma) / sd


def days_in_band(z: pd.Series, threshold: float, breakout_history: int = 30) -> pd.Series:
    """Count consecutive bars (ending at each bar) where |z| > threshold.

    Resets to 0 whenever z re-enters the band (|z| ≤ threshold). Capped at
    breakout_history.  This tracks "how long the current outside-band state has
    lasted"; 0 when z is currently inside the bands.
    """
    outside = (z.abs() > threshold).values
    result = np.zeros(len(z), dtype=int)
    count = 0
    for i, out in enumerate(outside):
        if out:
            count = min(count + 1, breakout_history)
        else:
            count = 0
        result[i] = count
    return pd.Series(result, index=z.index)
