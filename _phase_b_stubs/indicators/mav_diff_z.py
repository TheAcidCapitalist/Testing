"""MAV Diff Z-Score — fast/slow SMA difference normalised as a Z-score."""

from __future__ import annotations

import numpy as np
import pandas as pd

from scanner.indicators._base import BaseIndicator


class MAVDiffZ(BaseIndicator):
    """Z-score of fast/slow SMA difference.  See spec/indicators.md §8."""

    name = "mav_diff_z"

    def __init__(self, fast: int = 10, slow: int = 50, z_window: int = 252) -> None:
        self.fast = fast
        self.slow = slow
        self.z_window = z_window

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        sma_fast = df["close"].rolling(self.fast).mean()
        sma_slow = df["close"].rolling(self.slow).mean()

        diff_pct = (sma_fast - sma_slow) / df["close"].replace(0, np.nan)

        roll_mean = diff_pct.rolling(self.z_window).mean()
        roll_std = diff_pct.rolling(self.z_window).std()
        z = (diff_pct - roll_mean) / roll_std.replace(0, np.nan)

        df["mav_diff_pct"] = diff_pct
        df["mav_diff_z_value"] = z

        df["mav_diff_z_signal"] = np.where(
            z < -2, "extremely_oversold", np.where(z > 2, "extremely_overbought", "neutral")
        )

        # Score: 1 at z ≤ -2, 0 at z ≥ +2, linear between
        df["mav_diff_z_score"] = ((-z + 2) / 4).clip(0, 1)

        return df
