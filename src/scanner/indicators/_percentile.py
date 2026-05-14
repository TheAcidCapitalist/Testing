"""Shared percentile-rank helper for confirmation indicators.

Private module (leading underscore) — skipped by the registry.

Used by volatility.py and volume.py.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def percentile_rank(series: pd.Series, history: int) -> pd.Series:
    """Per-bar Excel PERCENTRANK over a rolling window.

    For each bar: count values strictly below the current value in the last
    ``history`` bars (including the current bar), then divide by (window_size − 1).

    Matches Excel PERCENTRANK semantics and reproduces the 2012 fixture values
    within 1e-3 for both the Volatility and Volume indicators.

    Uses min_periods=2 so short series return valid values from the second bar
    onward rather than all-NaN.

    Parameters
    ----------
    series:
        The raw values to rank (chronologically ascending).
    history:
        Rolling window size (default for both indicators: 180).
    """
    def _apply(arr: np.ndarray) -> float:
        cur = arr[-1]
        below = np.sum(arr < cur)
        return below / (len(arr) - 1)

    return series.rolling(window=history, min_periods=2).apply(_apply, raw=True)
