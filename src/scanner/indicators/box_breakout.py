"""Congestion / Box Breakout indicator.

Spec: spec/indicators.md §8

Detects extended price congestion (a tight horizontal range — "the box") followed
by a breakout beyond the box edge.  Complementary to MAV Breakout: MAV uses
moving-average band compression; this uses the literal horizontal price range.

Detection algorithm (single forward-pass state machine, O(n)):
  1. Bar 0 always starts the first run unconditionally.
  2. For each subsequent bar i, try to extend the current run:
       candidate_high = max(run_high, high[i])
       candidate_low  = min(run_low,  low[i])
       if tightness(candidate) <= max_range: bar fits → extend run, continue.
       else: run [run_start..i-1] has ended.
  3. When a run ends:
       if len(run) >= min_congestion_bars: valid box → check bar i for breakout.
         close > run_high + buffer_abs → breakout +1 (buy)
         close < run_low  - buffer_abs → breakout -1 (sell)
         else                          → no breakout (quiet exit)
       Start fresh run at bar i regardless of box validity.

Tightness metrics:
  "pct": (box_high - box_low) / ((box_high + box_low) / 2)
  "atr": (box_high - box_low) / ATR(atr_window)

Buffer:
  "pct": buffer_abs = breakout_buffer × midprice_of_box
  "atr": buffer_abs = breakout_buffer × ATR(at breakout bar)

Output per bar:
  box_active (bool), box_high (float|nan), box_low (float|nan),
  box_length (int|nan), breakout_dir (-1/0/+1),
  days_since_breakout (int|nan), direction, signal_value.

Signal_value three-value scheme (mirrors Bollinger):
  0.25 — fresh bullish breakout
  0.75 — fresh bearish breakout
  0.50 — no fresh breakout
"""

from __future__ import annotations

import numpy as np
import pandas as pd

NAME = "box_breakout"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _compute_atr(df: pd.DataFrame, atr_window: int) -> np.ndarray:
    """True Range and rolling ATR. NaN for the first bar (no prev close)."""
    high = df["high"].values.astype(float)
    low  = df["low"].values.astype(float)
    close = df["close"].values.astype(float)
    n = len(df)

    tr = np.empty(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))

    # Rolling mean with min_periods=atr_window; pre-fill with NaN.
    atr_series = pd.Series(tr).rolling(atr_window, min_periods=atr_window).mean()
    return atr_series.to_numpy()


def _tightness_pct(bh: float, bl: float) -> float:
    mid = (bh + bl) / 2.0
    return (bh - bl) / mid if mid > 0 else float("inf")


def _run_detection(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    atr: np.ndarray | None,
    *,
    min_congestion_bars: int,
    max_range: float,
    range_metric: str,
    breakout_buffer: float,
    breakout_recency: int,
) -> dict:
    """Core state machine.  Returns dict of per-bar arrays."""
    n = len(close)

    # Per-bar output arrays
    box_active_arr        = np.zeros(n, dtype=bool)
    box_high_arr          = np.full(n, np.nan)
    box_low_arr           = np.full(n, np.nan)
    box_length_arr        = np.full(n, np.nan)
    breakout_dir_arr      = np.zeros(n, dtype=int)
    days_since_arr        = np.full(n, np.nan)

    # Last completed valid box (for non-active bars)
    last_box_high: float | None = None
    last_box_low:  float | None = None
    last_box_len:  int   | None = None
    last_breakout_bar: int | None = None

    # Run state
    run_start = 0
    run_high = high[0]
    run_low  = low[0]

    def _tight(bh: float, bl: float, i: int) -> float:
        if range_metric == "pct":
            return _tightness_pct(bh, bl)
        # atr
        a = atr[i] if atr is not None else 1.0  # type: ignore[index]
        return (bh - bl) / a if a > 0 else float("inf")

    def _buf(i: int) -> float:
        if range_metric == "pct":
            mid = (run_high + run_low) / 2.0
            return breakout_buffer * mid
        # atr
        a = atr[i] if atr is not None else 1.0  # type: ignore[index]
        return breakout_buffer * a

    def _fill_bar(i: int, r_high: float, r_low: float, r_len: int) -> None:
        """Mark bar i as inside a valid in-progress run."""
        box_active_arr[i] = True
        box_high_arr[i]   = r_high
        box_low_arr[i]    = r_low
        box_length_arr[i] = r_len

    # ---- main loop --------------------------------------------------------
    for i in range(1, n):
        cand_high = max(run_high, high[i])
        cand_low  = min(run_low,  low[i])

        if _tight(cand_high, cand_low, i) <= max_range:
            # Bar i fits — extend run
            run_high = cand_high
            run_low  = cand_low
            run_len = i - run_start + 1  # +1 includes bar i
            if run_len >= min_congestion_bars:
                _fill_bar(i, run_high, run_low, run_len)
            # days_since (from last breakout, if any)
            if last_breakout_bar is not None:
                days_since_arr[i] = i - last_breakout_bar
        else:
            # Bar i does NOT fit — run [run_start .. i-1] has ended
            run_len = i - run_start  # number of bars in completed run

            if run_len >= min_congestion_bars:
                # Valid box — check bar i for breakout
                buf = _buf(i)
                if close[i] > run_high + buf:
                    breakout_dir_arr[i] = 1
                    last_breakout_bar = i
                elif close[i] < run_low - buf:
                    breakout_dir_arr[i] = -1
                    last_breakout_bar = i
                # Record last completed valid box (regardless of breakout outcome)
                last_box_high = run_high
                last_box_low  = run_low
                last_box_len  = run_len

            # Start fresh run at bar i
            run_start = i
            run_high  = high[i]
            run_low   = low[i]

            if last_breakout_bar is not None:
                days_since_arr[i] = i - last_breakout_bar

    # ---- post-process: build direction + signal_value ----------------------
    direction_arr   = np.full(n, "neutral", dtype=object)
    signal_val_arr  = np.full(n, 0.5)

    for i in range(n):
        d = int(breakout_dir_arr[i])
        if d != 0:
            # This is a breakout bar
            if d == 1:
                direction_arr[i]  = "buy"
                signal_val_arr[i] = 0.25
            else:
                direction_arr[i]  = "sell"
                signal_val_arr[i] = 0.75
        elif not np.isnan(days_since_arr[i]):
            since = int(days_since_arr[i])
            if since <= breakout_recency:
                # Find the direction of that previous breakout
                breakout_at = i - since
                bd = int(breakout_dir_arr[breakout_at])
                if bd == 1:
                    direction_arr[i]  = "buy"
                    signal_val_arr[i] = 0.25
                else:
                    direction_arr[i]  = "sell"
                    signal_val_arr[i] = 0.75

    return {
        "box_active":        box_active_arr,
        "box_high":          box_high_arr,
        "box_low":           box_low_arr,
        "box_length":        box_length_arr,
        "breakout_dir":      breakout_dir_arr,
        "days_since":        days_since_arr,
        "direction":         direction_arr,
        "signal_value":      signal_val_arr,
        # scalars for compute()
        "_last_box_high":    last_box_high,
        "_last_box_low":     last_box_low,
        "_last_box_len":     last_box_len,
        "_last_breakout_bar": last_breakout_bar,
        "_run_start":        run_start,
        "_run_high":         run_high,
        "_run_low":          run_low,
        "_n":                n,
    }


def _build_series_df(res: dict) -> pd.DataFrame:
    return pd.DataFrame({
        "box_active":        res["box_active"],
        "box_high":          res["box_high"],
        "box_low":           res["box_low"],
        "box_length":        res["box_length"],
        "breakout_dir":      res["breakout_dir"],
        "days_since_breakout": res["days_since"],
        "direction":         res["direction"],
        "signal_value":      res["signal_value"],
    })


def _latest_bar_dict(
    res: dict,
    df: pd.DataFrame,
    min_congestion_bars: int,
) -> dict:
    """Extract the latest-bar summary dict from detection results."""
    n = res["_n"]
    last = n - 1

    # Box info: prefer in-progress valid run, else last completed valid box.
    run_len_now = last - res["_run_start"] + 1
    if run_len_now >= min_congestion_bars:
        box_high = res["_run_high"]
        box_low  = res["_run_low"]
        box_len  = run_len_now
    elif res["_last_box_high"] is not None:
        box_high = res["_last_box_high"]
        box_low  = res["_last_box_low"]
        box_len  = res["_last_box_len"]
    else:
        box_high = None
        box_low  = None
        box_len  = None

    # days_since_breakout
    lb = res["_last_breakout_bar"]
    days_since: int | None = (last - lb) if lb is not None else None

    return {
        "signal_value":       float(res["signal_value"][last]),
        "direction":          str(res["direction"][last]),
        "box_high":           float(box_high) if box_high is not None else None,
        "box_low":            float(box_low)  if box_low  is not None else None,
        "box_length":         int(box_len)    if box_len  is not None else None,
        "days_since_breakout": days_since,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_series(
    df: pd.DataFrame,
    *,
    min_congestion_bars: int = 15,
    max_range: float = 0.06,
    range_metric: str = "pct",
    atr_window: int = 14,
    breakout_buffer: float = 0.25,
    breakout_recency: int = 3,
) -> pd.DataFrame:
    """Return per-bar DataFrame: box_active, box_high, box_low, box_length,
    breakout_dir, days_since_breakout, direction, signal_value.

    Parameters
    ----------
    df:
        Chronologically-ascending OHLCV DataFrame.
    min_congestion_bars:
        Minimum bars for a congestion zone to be a valid box (default 15).
    max_range:
        Maximum tightness for the box (default 0.06 = 6% of midprice for
        range_metric="pct"; interpret as ATR multiples for "atr").
    range_metric:
        "pct" (default) or "atr".
    atr_window:
        ATR lookback, only used when range_metric="atr" (default 14).
    breakout_buffer:
        Close must clear the box edge by this much (in the same units as
        range_metric) to count as a breakout (default 0.25).
    breakout_recency:
        A breakout stays "fresh" for this many bars (default 3).
    """
    df = df.reset_index(drop=True)
    atr = _compute_atr(df, atr_window) if range_metric == "atr" else None
    res = _run_detection(
        df["high"].values.astype(float),
        df["low"].values.astype(float),
        df["close"].values.astype(float),
        atr,
        min_congestion_bars=min_congestion_bars,
        max_range=max_range,
        range_metric=range_metric,
        breakout_buffer=breakout_buffer,
        breakout_recency=breakout_recency,
    )
    return _build_series_df(res)


def compute(
    df: pd.DataFrame,
    *,
    min_congestion_bars: int = 15,
    max_range: float = 0.06,
    range_metric: str = "pct",
    atr_window: int = 14,
    breakout_buffer: float = 0.25,
    breakout_recency: int = 3,
) -> dict:
    """Return the latest-bar box breakout result.

    Returns
    -------
    dict with keys:
        signal_value (float), direction ("buy"|"sell"|"neutral"),
        box_high (float|None), box_low (float|None),
        box_length (int|None), days_since_breakout (int|None).
    """
    df = df.reset_index(drop=True)
    atr = _compute_atr(df, atr_window) if range_metric == "atr" else None
    res = _run_detection(
        df["high"].values.astype(float),
        df["low"].values.astype(float),
        df["close"].values.astype(float),
        atr,
        min_congestion_bars=min_congestion_bars,
        max_range=max_range,
        range_metric=range_metric,
        breakout_buffer=breakout_buffer,
        breakout_recency=breakout_recency,
    )
    return _latest_bar_dict(res, df, min_congestion_bars)
