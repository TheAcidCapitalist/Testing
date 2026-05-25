"""Congestion / Box Breakout indicator.

Spec: spec/indicators.md §8

Detects extended price congestion (a tight horizontal range — "the box") followed
by a breakout beyond the box edge.

Detection algorithm (fixed-lookback %-duration):
  For each bar i, examine the preceding window W = [i - lookback, i - 1].
  Calculate box_high and box_low over W.
  A box is valid if its overall range is tight relative to typical daily volatility:
    (box_high - box_low) / mean(ATR over W) <= compression_threshold
  AND it meets the proximity duration:
    count of bars near box_high >= duration_pct * lookback (bullish)
    count of bars near box_low >= duration_pct * lookback (bearish)
  we have a valid box. 
  A breakout happens if the current close breaks the box_high/box_low with a buffer.

Output per bar:
  box_high (float|nan), box_low (float|nan), box_length (int|nan), 
  days_since_breakout (int|nan), volume_expansion (bool), direction, signal_value.

Signal_value three-value scheme:
  0.25 — fresh bullish breakout
  0.75 — fresh bearish breakout
  0.50 — no fresh breakout
"""

from __future__ import annotations

import numpy as np
import pandas as pd

NAME = "box_breakout"

def _compute_atr(df: pd.DataFrame, atr_window: int) -> pd.Series:
    """True Range and rolling ATR. NaN for the first bar (no prev close)."""
    high = df["high"].values.astype(float)
    low  = df["low"].values.astype(float)
    close = df["close"].values.astype(float)
    n = len(df)

    tr = np.empty(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))

    return pd.Series(tr).rolling(atr_window, min_periods=atr_window).mean()


def _run_detection(df: pd.DataFrame, params: dict) -> dict:
    high = df["high"].values.astype(float)
    low = df["low"].values.astype(float)
    close = df["close"].values.astype(float)
    vol = df["volume"].values.astype(float)
    n = len(df)

    atr_short = _compute_atr(df, params["atr_window"]).values

    vol_sma = pd.Series(vol).rolling(params["vol_window"], min_periods=params["vol_window"]).mean().values

    lookback = params["lookback"]
    duration_bars = int(np.ceil(lookback * params["duration_pct"]))

    box_high_arr = np.full(n, np.nan)
    box_low_arr = np.full(n, np.nan)
    box_len_arr = np.full(n, np.nan)
    breakout_dir_arr = np.zeros(n, dtype=int)
    days_since_arr = np.full(n, np.nan)
    vol_exp_arr = np.zeros(n, dtype=bool)

    last_breakout_bar = None
    last_box_high = None
    last_box_low = None

    for i in range(lookback, n):
        w_start = max(0, i - lookback)
        w_closes = close[w_start:i]
        box_high = np.max(high[w_start:i])
        box_low = np.min(low[w_start:i])
        box_range = box_high - box_low

        w_atr = atr_short[w_start:i]
        valid_w_atr = w_atr[~np.isnan(w_atr)]
        if len(valid_w_atr) > 0:
            atr_mean = np.mean(valid_w_atr)
            is_compressed = (atr_mean > 0) and ((box_range / atr_mean) <= params["compression_threshold"])
        else:
            is_compressed = False

        # Bullish
        bull_prox = w_closes >= box_high * (1.0 - params["touch_tolerance"])
        bull_count = np.sum(bull_prox)
        valid_bull = is_compressed and (bull_count >= duration_bars)

        # Bearish
        bear_prox = w_closes <= box_low * (1.0 + params["touch_tolerance"])
        bear_count = np.sum(bear_prox)
        valid_bear = is_compressed and (bear_count >= duration_bars)

        is_breakout = 0
        if valid_bull and close[i] > box_high * (1.0 + params["breakout_buffer"]):
            is_breakout = 1
        elif valid_bear and close[i] < box_low * (1.0 - params["breakout_buffer"]):
            is_breakout = -1

        if is_breakout != 0:
            breakout_dir_arr[i] = is_breakout
            last_breakout_bar = i
            last_box_high = box_high
            last_box_low = box_low

            # Check volume expansion against trailing average up to i-1
            if i - 1 >= 0 and not np.isnan(vol_sma[i-1]):
                if vol[i] >= params["vol_mult"] * vol_sma[i-1]:
                    vol_exp_arr[i] = True

        if last_breakout_bar is not None:
            days_since_arr[i] = i - last_breakout_bar

        if valid_bull or valid_bear:
            box_high_arr[i] = box_high
            box_low_arr[i] = box_low
            box_len_arr[i] = lookback
        elif last_box_high is not None:
            box_high_arr[i] = last_box_high
            box_low_arr[i] = last_box_low
            box_len_arr[i] = lookback

    # Build direction and signal_value
    direction_arr = np.full(n, "neutral", dtype=object)
    signal_val_arr = np.full(n, 0.5)

    for i in range(n):
        d = breakout_dir_arr[i]
        if d == 1:
            direction_arr[i] = "buy"
            signal_val_arr[i] = 0.25
        elif d == -1:
            direction_arr[i] = "sell"
            signal_val_arr[i] = 0.75
        elif not np.isnan(days_since_arr[i]):
            since = int(days_since_arr[i])
            if since <= params["breakout_recency"]:
                bd = breakout_dir_arr[i - since]
                if bd == 1:
                    direction_arr[i] = "buy"
                    signal_val_arr[i] = 0.25
                elif bd == -1:
                    direction_arr[i] = "sell"
                    signal_val_arr[i] = 0.75

    return {
        "box_high": box_high_arr,
        "box_low": box_low_arr,
        "box_length": box_len_arr,
        "days_since_breakout": days_since_arr,
        "volume_expansion": vol_exp_arr,
        "direction": direction_arr,
        "signal_value": signal_val_arr,
        "_n": n,
        "_last_breakout_bar": last_breakout_bar,
    }

def _build_series_df(res: dict) -> pd.DataFrame:
    return pd.DataFrame({
        "box_high":          res["box_high"],
        "box_low":           res["box_low"],
        "box_length":        res["box_length"],
        "days_since_breakout": res["days_since_breakout"],
        "volume_expansion":  res["volume_expansion"],
        "direction":         res["direction"],
        "signal_value":      res["signal_value"],
    })

def _latest_bar_dict(res: dict) -> dict:
    last = res["_n"] - 1
    box_high = res["box_high"][last]
    box_low = res["box_low"][last]
    box_len = res["box_length"][last]
    days_since = res["days_since_breakout"][last]
    
    return {
        "signal_value":       float(res["signal_value"][last]),
        "direction":          str(res["direction"][last]),
        "box_high":           float(box_high) if not np.isnan(box_high) else None,
        "box_low":            float(box_low) if not np.isnan(box_low) else None,
        "box_length":         int(box_len) if not np.isnan(box_len) else None,
        "days_since_breakout": int(days_since) if not np.isnan(days_since) else None,
        "volume_expansion":   bool(res["volume_expansion"][last]),
    }

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_series(
    df: pd.DataFrame,
    *,
    lookback: int = 60,
    duration_pct: float = 0.75,
    touch_tolerance: float = 0.05,
    compression_threshold: float = 5.0,
    atr_window: int = 14,
    breakout_buffer: float = 0.0,
    vol_mult: float = 1.5,
    vol_window: int = 20,
    breakout_recency: int = 3,
    mode: str = "confirmed",
) -> pd.DataFrame:
    df = df.reset_index(drop=True)
    if mode != "confirmed":
        pass # stubbed/deferred

    params = {
        "lookback": lookback,
        "duration_pct": duration_pct,
        "touch_tolerance": touch_tolerance,
        "compression_threshold": compression_threshold,
        "atr_window": atr_window,
        "breakout_buffer": breakout_buffer,
        "vol_mult": vol_mult,
        "vol_window": vol_window,
        "breakout_recency": breakout_recency,
    }
    res = _run_detection(df, params)
    return _build_series_df(res)


def compute(
    df: pd.DataFrame,
    *,
    lookback: int = 60,
    duration_pct: float = 0.75,
    touch_tolerance: float = 0.05,
    compression_threshold: float = 5.0,
    atr_window: int = 14,
    breakout_buffer: float = 0.0,
    vol_mult: float = 1.5,
    vol_window: int = 20,
    breakout_recency: int = 3,
    mode: str = "confirmed",
) -> dict:
    df = df.reset_index(drop=True)
    params = {
        "lookback": lookback,
        "duration_pct": duration_pct,
        "touch_tolerance": touch_tolerance,
        "compression_threshold": compression_threshold,
        "atr_window": atr_window,
        "breakout_buffer": breakout_buffer,
        "vol_mult": vol_mult,
        "vol_window": vol_window,
        "breakout_recency": breakout_recency,
    }
    res = _run_detection(df, params)
    return _latest_bar_dict(res)
