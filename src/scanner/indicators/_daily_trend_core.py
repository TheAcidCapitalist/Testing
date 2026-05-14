"""Shared Daily Trend slope computation used by the Divergence and Contrarian variants.

This module is private (leading underscore) so the registry skips it.

Slope formula
-------------
slope[t] = (MA[t] − MA[t−1]) / MA[t]

where MA is a simple rolling mean over `ma_window` bars.  Dividing by MA[t]
(not MA[t−1]) matches the 2012 spreadsheet fixture to floating-point precision.

The `price_series` parameter selects which OHLCV column to use; defaults to
"close" but the spec allows "high" or "low" as well.
"""

from __future__ import annotations

import pandas as pd


def ma_slope_series(
    df: pd.DataFrame,
    ma_window: int = 21,
    price_series: str = "close",
) -> pd.Series:
    """Compute the per-bar percentage-change slope of the rolling MA.

    Parameters
    ----------
    df:
        Chronologically-ascending OHLCV DataFrame.
    ma_window:
        Simple moving-average window (default 21).
    price_series:
        Column to use: "close" (default), "high", or "low".

    Returns
    -------
    pd.Series of slope values, NaN for bars where the MA window or the
    shift is not yet fully populated (first ma_window bars are NaN, plus
    one extra NaN from the shift).
    """
    price = df[price_series].astype(float).reset_index(drop=True)
    ma = price.rolling(ma_window).mean()
    slope = (ma - ma.shift(1)) / ma
    return slope
