"""MAV Difference Z-Score — confirmation indicator (exit signal only).

Spec: spec/indicators.md §9

Two moving averages of the close price; the z-score of their difference over a
rolling history measures the magnitude and direction of trend acceleration.  A
sign change in the z-score signals trend reversal (used as a backtest exit in
Phase E — does NOT feed the v1 live-scan combo score or ranking).

Computation:
  1. mav1 = SMA(close, mav1)     [default 20]
  2. mav2 = SMA(close, mav2)     [default 50]
  3. diff  = mav1 − mav2
  4. z     = (diff − rolling_mean(diff, z_history)) / rolling_std(diff, z_history, ddof=1)
  5. reversal flag fires when sign(z) flips from one non-zero sign to the other.

MA type: **simple moving average (SMA)**.  "MAV" throughout the original
spreadsheet always means SMA (Daily Trend, MAV Breakout both use SMA).  The
word "exponential" in the source description ("exponential move/acceleration")
refers to what the indicator measures, not the MA type.  No EMA is named for
any indicator in the spreadsheet.

Zero-touch behaviour: exactly-zero z is treated as "no sign" — neither positive
nor negative.  The reversal flag fires on the first bar with a clearly non-zero
sign opposite to the most recent non-zero sign.  Concretely, for a sequence
z = [..., +0.5, 0.0, −0.3, ...]:
  • At z = 0.0:  no reversal (zero carries no sign, does not update sign memory).
  • At z = −0.3: reversal fires (first clearly-negative bar after a positive one).

This differs from a simple consecutive-bar comparison (z[i-1] vs z[i]) — it
tracks the last *non-zero* z sign, so a single zero bar between two opposite
signs does not suppress the reversal; the reversal fires at the next non-zero bar.

Output contract:
  This indicator does NOT fit the {percentile, state} shape used by Volatility
  and Volume.  It produces a z-score (standard deviations from the rolling mean)
  and a boolean reversal flag.  compute() returns:
    {
        "mav_diff":   float | None,   # mav1 − mav2 (raw difference, latest bar)
        "z_score":    float | None,   # z-score (None during warmup)
        "reversal":   bool,           # True on the bar where sign changes
        "mav1_value": float | None,   # current SMA(mav1)
        "mav2_value": float | None,   # current SMA(mav2)
    }

Warmup: the first mav2 + z_history − 2 bars have z = NaN and reversal = False.
"""

from __future__ import annotations

import pandas as pd

NAME = "mav_diff_z"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _detect_reversals(z: pd.Series) -> list[bool]:
    """Return a per-bar list of booleans indicating sign-change reversal.

    Rules:
    - NaN → no reversal, sign memory unchanged.
    - z == 0 → no reversal, sign memory unchanged (zero = "no sign").
    - z != 0 and != NaN → if last non-zero sign exists and differs → reversal.
      Always update sign memory when z is non-zero.
    """
    result: list[bool] = []
    last_nonzero_sign: int = 0  # 0 = "no prior sign"

    for zi in z:
        if pd.isna(zi):
            result.append(False)
        elif zi == 0.0:
            result.append(False)
        else:
            curr_sign = 1 if zi > 0 else -1
            fired = last_nonzero_sign != 0 and curr_sign != last_nonzero_sign
            result.append(fired)
            last_nonzero_sign = curr_sign

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_series(
    df: pd.DataFrame,
    *,
    mav1: int = 20,
    mav2: int = 50,
    z_history: int = 180,
) -> pd.DataFrame:
    """Return a per-bar DataFrame: mav1_value, mav2_value, mav_diff, z_score, reversal.

    Parameters
    ----------
    df:
        Chronologically-ascending OHLCV DataFrame.
    mav1:
        Window for the faster SMA (default 20).
    mav2:
        Window for the slower SMA (default 50).
    z_history:
        Rolling window for z-score computation (default 180).
    """
    df = df.reset_index(drop=True)
    close = df["close"].astype(float)

    ma1 = close.rolling(mav1).mean()
    ma2 = close.rolling(mav2).mean()
    diff = ma1 - ma2
    roll_mean = diff.rolling(z_history).mean()
    roll_std = diff.rolling(z_history).std(ddof=1)
    z = (diff - roll_mean) / roll_std

    reversals = _detect_reversals(z)

    return pd.DataFrame({
        "mav1_value": ma1.values,
        "mav2_value": ma2.values,
        "mav_diff":   diff.values,
        "z_score":    z.values,
        "reversal":   reversals,
    })


def compute(
    df: pd.DataFrame,
    *,
    mav1: int = 20,
    mav2: int = 50,
    z_history: int = 180,
) -> dict:
    """Return the latest-bar MAV Difference Z-Score result.

    Returns
    -------
    dict with keys: mav_diff (float|None), z_score (float|None),
    reversal (bool), mav1_value (float|None), mav2_value (float|None).
    """
    series = compute_series(df, mav1=mav1, mav2=mav2, z_history=z_history)
    last = series.iloc[-1]

    def _maybe(val: float) -> float | None:
        return None if pd.isna(val) else float(val)

    return {
        "mav_diff":   _maybe(last["mav_diff"]),
        "z_score":    _maybe(last["z_score"]),
        "reversal":   bool(last["reversal"]),
        "mav1_value": _maybe(last["mav1_value"]),
        "mav2_value": _maybe(last["mav2_value"]),
    }
