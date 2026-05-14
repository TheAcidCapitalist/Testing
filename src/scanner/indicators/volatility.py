"""Volatility — realized-volatility percentile (confirmation indicator).

Spec: spec/indicators.md §10

This is a **confirmation indicator**: it emits {percentile, state} rather than
{signal_value, direction}.  It does not trigger trade signals; it confirms,
demotes, or flags-for-exit signals produced by the trade indicators.

State logic (opposite of Volume — low vol = confirmed trend):
  confirm — percentile < confirm_threshold  (default 0.3)
  reject  — percentile > reversal_threshold (default 0.7)
  neutral — otherwise

Two-stage computation
---------------------
1. Realized volatility (RV) — measured over a short window (~21 bars / 1M).
   Source priority:
     a. If ``realized_vol`` column is present in the input DataFrame, use it
        directly (fixture CSVs carry Bloomberg's RV; production may precompute
        and attach it).
     b. Otherwise, compute via ``compute_realized_vol(closes)`` — annualized
        std of log returns over ``rv_window`` bars.  This is a reasonable
        production stand-in; it is NOT expected to match Bloomberg's values.

2. Percentile rank — RV's rank within the last ``history`` bars.
   Formula: count_strictly_below / (window_size − 1)
   This matches Excel PERCENTRANK semantics and reproduces the 2012 fixture
   values within 1e-3 on all five tickers.

   When fewer than ``history`` bars are available, the computation uses all
   available data (min_periods=2), so short series degrade gracefully.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from scanner.indicators._percentile import percentile_rank

NAME = "volatility"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def compute_realized_vol(closes: pd.Series, rv_window: int = 21) -> pd.Series:
    """Annualized realized volatility from log returns, in percent.

    Standard formula: σ_annual = std(log_returns, ddof=1) * sqrt(252) * 100.

    This is a production stand-in used when the input DataFrame does not contain
    a pre-computed ``realized_vol`` column.  It is NOT matched against the fixture
    (the fixture uses Bloomberg's RV as a black box); it simply provides a
    reasonable RV estimate for live scanning.

    Parameters
    ----------
    closes:
        Chronologically-ascending close prices.
    rv_window:
        Rolling window for the std computation (default 21 ≈ 1 calendar month).
    """
    log_ret = np.log(closes / closes.shift(1))
    return log_ret.rolling(rv_window).std(ddof=1) * np.sqrt(252) * 100


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_series(
    df: pd.DataFrame,
    *,
    rv_window: int = 21,
    history: int = 180,
    confirm_threshold: float = 0.3,
    reversal_threshold: float = 0.7,
) -> pd.DataFrame:
    """Return a per-bar DataFrame with columns: percentile, state.

    Parameters
    ----------
    df:
        Chronologically-ascending OHLCV DataFrame.  If a ``realized_vol`` column
        is present it is used directly; otherwise RV is computed from closes.
    rv_window:
        Window for ``compute_realized_vol`` (only used when ``realized_vol`` is
        absent from the DataFrame; default 21).
    history:
        Number of bars over which to compute the percentile rank (default 180).
    confirm_threshold:
        Percentile below which state = "confirm" (default 0.3).
    reversal_threshold:
        Percentile above which state = "reject" (default 0.7).
    """
    if "realized_vol" in df.columns:
        rv = df["realized_vol"].astype(float).reset_index(drop=True)
    else:
        close = df["close"].astype(float).reset_index(drop=True)
        rv = compute_realized_vol(close, rv_window=rv_window)

    pct = percentile_rank(rv, history)

    state = np.where(
        pct < confirm_threshold, "confirm",
        np.where(pct > reversal_threshold, "reject", "neutral"),
    )

    return pd.DataFrame({"percentile": pct.values, "state": state})


def compute(
    df: pd.DataFrame,
    *,
    rv_window: int = 21,
    history: int = 180,
    confirm_threshold: float = 0.3,
    reversal_threshold: float = 0.7,
) -> dict:
    """Return the latest-bar volatility confirmation result.

    Returns
    -------
    dict with keys: percentile (float), state ("confirm" | "neutral" | "reject").
    """
    series = compute_series(
        df,
        rv_window=rv_window,
        history=history,
        confirm_threshold=confirm_threshold,
        reversal_threshold=reversal_threshold,
    )
    last = series.iloc[-1]
    return {
        "percentile": float(last["percentile"]),
        "state": str(last["state"]),
    }
