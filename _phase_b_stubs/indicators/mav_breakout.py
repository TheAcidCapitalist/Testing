"""MAV Breakout — SMA golden/death cross event signal."""

from __future__ import annotations

import numpy as np
import pandas as pd

from scanner.indicators._base import BaseIndicator


class MAVBreakout(BaseIndicator):
    """Short/long SMA crossover event.  See spec/indicators.md §3."""

    name = "mav_breakout"

    def __init__(self, short_period: int = 20, long_period: int = 50) -> None:
        self.short_period = short_period
        self.long_period = long_period

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        sma_short = df["close"].rolling(self.short_period).mean()
        sma_long = df["close"].rolling(self.long_period).mean()

        above = sma_short > sma_long
        breakout_up = above & ~above.shift(1).fillna(False)
        breakout_down = ~above & above.shift(1).fillna(True)

        df["mav_breakout_short"] = sma_short
        df["mav_breakout_long"] = sma_long

        df["mav_breakout_signal"] = np.where(
            breakout_up,
            "breakout_up",
            np.where(breakout_down, "breakout_down", "none"),
        )

        # Binary: 1 on bullish breakout day only
        df["mav_breakout_score"] = breakout_up.astype(float)

        return df
