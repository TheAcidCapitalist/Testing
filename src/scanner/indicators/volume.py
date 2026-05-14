"""Volume — volume percentile (confirmation indicator).

Spec: spec/indicators.md §11

This is a **confirmation indicator**: it emits {percentile, state} rather than
{signal_value, direction}.  It does not trigger trade signals; it confirms,
demotes, or flags-for-exit signals produced by the trade indicators.

State logic (opposite of Volatility — high volume = confirmed trend):
  confirm — percentile > confirm_threshold  (default 0.7)
  reject  — percentile < reversal_threshold (default 0.3)
  neutral — otherwise

No two-stage computation: volume is already a raw OHLCV field.  The percentile
rank of the current bar's volume is computed directly against a single rolling
history (default 180 bars).

The v1 ranking decision (low volume demotes rather than removes a signal) is
handled in the scoring layer, not here.  This indicator just reports the
percentile and its state.

Percentile formula
------------------
Excel PERCENTRANK: count_strictly_below / (window_size − 1).
Shared with the Volatility indicator via _percentile.py.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from scanner.indicators._percentile import percentile_rank

NAME = "volume"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_series(
    df: pd.DataFrame,
    *,
    history: int = 180,
    confirm_threshold: float = 0.7,
    reversal_threshold: float = 0.3,
) -> pd.DataFrame:
    """Return a per-bar DataFrame with columns: percentile, state.

    Parameters
    ----------
    df:
        Chronologically-ascending OHLCV DataFrame.
    history:
        Number of bars over which to compute the percentile rank (default 180).
    confirm_threshold:
        Percentile above which state = "confirm" (default 0.7).
    reversal_threshold:
        Percentile below which state = "reject" (default 0.3).
    """
    volume = df["volume"].astype(float).reset_index(drop=True)
    pct = percentile_rank(volume, history)

    state = np.where(
        pct > confirm_threshold, "confirm",
        np.where(pct < reversal_threshold, "reject", "neutral"),
    )

    return pd.DataFrame({"percentile": pct.values, "state": state})


def compute(
    df: pd.DataFrame,
    *,
    history: int = 180,
    confirm_threshold: float = 0.7,
    reversal_threshold: float = 0.3,
) -> dict:
    """Return the latest-bar volume confirmation result.

    Returns
    -------
    dict with keys: percentile (float), state ("confirm" | "neutral" | "reject").
    """
    series = compute_series(
        df,
        history=history,
        confirm_threshold=confirm_threshold,
        reversal_threshold=reversal_threshold,
    )
    last = series.iloc[-1]
    return {
        "percentile": float(last["percentile"]),
        "state": str(last["state"]),
    }
