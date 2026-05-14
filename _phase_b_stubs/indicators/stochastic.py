"""Stochastic Oscillator (%K / %D)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from scanner.indicators._base import BaseIndicator


class Stochastic(BaseIndicator):
    """Slow stochastic with smoothed %K.  See spec/indicators.md §2."""

    name = "stochastic"

    def __init__(
        self,
        k_period: int = 14,
        d_period: int = 3,
        smooth_k: int = 3,
    ) -> None:
        self.k_period = k_period
        self.d_period = d_period
        self.smooth_k = smooth_k

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        low_min = df["low"].rolling(self.k_period).min()
        high_max = df["high"].rolling(self.k_period).max()

        raw_k = 100 * (df["close"] - low_min) / (high_max - low_min).replace(0, np.nan)
        k = raw_k.rolling(self.smooth_k).mean()  # smoothed %K
        d = k.rolling(self.d_period).mean()      # %D

        df["stoch_k"] = k
        df["stoch_d"] = d

        df["stochastic_signal"] = np.where(
            k < 20, "oversold", np.where(k > 80, "overbought", "neutral")
        )

        # Score: 1 when oversold and %K > %D (turning up), 0 when overbought
        rising = (k > d).astype(float)
        raw_score = (20 - k) / 20  # peaks at 1 when k=0
        df["stochastic_score"] = (raw_score.clip(0, 1) * rising).clip(0, 1)

        return df
