"""Bollinger Band — Normal (trend-following / breakout).

Spec: spec/indicators.md §6

Direction logic:
  buy    — z-score rises above +sd_threshold  (default +1.5)
  sell   — z-score falls below −sd_threshold  (default −1.5)
  neutral — otherwise

bollinger_days counts the number of consecutive bars the current outside-band
state has lasted (capped at breakout_history).  Resets to 0 when z returns
inside the bands.

Shares z-score computation with bollinger_contrarian via _bollinger_core.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from scanner.indicators._bollinger_core import bollinger_z_series, days_in_band

NAME = "bollinger_normal"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_series(
    df: pd.DataFrame,
    *,
    z_days: int = 21,
    sd_threshold: float = 1.5,
    contrarian_threshold: float = 0.25,  # noqa: ARG001 — owned by contrarian variant
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
        Buy when z > +threshold; sell when z < −threshold (default 1.5).
    contrarian_threshold:
        Passed through for API uniformity; used by the Contrarian variant.
    breakout_history:
        Cap for bollinger_days counter (default 30).
    """
    close = df["close"].astype(float).reset_index(drop=True)
    z = bollinger_z_series(close, z_days)
    b_days = days_in_band(z, sd_threshold, breakout_history)

    direction = np.where(
        z > sd_threshold, "buy",
        np.where(z < -sd_threshold, "sell", "neutral"),
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
    """Return the latest-bar Bollinger Normal result.

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
