"""Bollinger Band — Contrarian (mean-reversion).

Spec: spec/indicators.md §7

Uses the same z-score as Normal but inverts the signal logic.  A new band is
derived at (sd_threshold − contrarian_threshold), default 1.5 − 0.25 = 1.25:

  buy    — z falls below −contrarian_band  (price stretched low → fade downside)
  sell   — z rises above +contrarian_band  (price stretched high → fade upside)
  neutral — otherwise

This is the opposite of Normal: where Normal buys a high-z breakout, Contrarian
sells it (and vice versa).

Shares z-score computation with bollinger_normal via _bollinger_core.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from scanner.indicators._bollinger_core import bollinger_z_series, days_in_band

NAME = "bollinger_contrarian"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_series(
    df: pd.DataFrame,
    *,
    z_days: int = 21,
    sd_threshold: float = 1.5,
    contrarian_threshold: float = 0.25,
    breakout_history: int = 30,
) -> pd.DataFrame:
    """Return a per-bar DataFrame with columns: bollinger_z, bollinger_days, direction.

    Parameters
    ----------
    df:
        Chronologically-ascending OHLCV DataFrame.
    z_days:
        Rolling window for MA and σ (default 21).
    sd_threshold:
        Outer Normal threshold (1.5); contrarian band = sd_threshold − contrarian_threshold.
    contrarian_threshold:
        Narrowing from sd_threshold to form the contrarian band (default 0.25 → band = 1.25).
    breakout_history:
        Cap for bollinger_days counter (default 30).
    """
    close = df["close"].astype(float).reset_index(drop=True)
    z = bollinger_z_series(close, z_days)
    contrarian_band = sd_threshold - contrarian_threshold  # default 1.25

    b_days = days_in_band(z, contrarian_band, breakout_history)

    direction = np.where(
        z < -contrarian_band, "buy",
        np.where(z > contrarian_band, "sell", "neutral"),
    )

    return pd.DataFrame(
        {
            "bollinger_z": z.values,
            "bollinger_days": b_days.values,
            "direction": direction,
        }
    )


def compute(
    df: pd.DataFrame,
    *,
    z_days: int = 21,
    sd_threshold: float = 1.5,
    contrarian_threshold: float = 0.25,
    breakout_history: int = 30,
) -> dict:
    """Return the latest-bar Bollinger Contrarian result.

    Returns
    -------
    dict with keys: signal_value, direction, bollinger_z, bollinger_days.
    """
    series = compute_series(
        df,
        z_days=z_days,
        sd_threshold=sd_threshold,
        contrarian_threshold=contrarian_threshold,
        breakout_history=breakout_history,
    )
    last = series.iloc[-1]
    return {
        "signal_value": float(last["bollinger_z"]),
        "direction": str(last["direction"]),
        "bollinger_z": float(last["bollinger_z"]),
        "bollinger_days": int(last["bollinger_days"]),
    }
