"""Bollinger Bands with %B score."""

from __future__ import annotations

import pandas as pd

from scanner.indicators._base import BaseIndicator


class BollingerBands(BaseIndicator):
    """20-period Bollinger Bands.  See spec/indicators.md §5."""

    name = "bollinger"

    def __init__(self, period: int = 20, std_dev: float = 2.0) -> None:
        self.period = period
        self.std_dev = std_dev

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        mid = df["close"].rolling(self.period).mean()
        std = df["close"].rolling(self.period).std()

        upper = mid + self.std_dev * std
        lower = mid - self.std_dev * std
        band_width = (upper - lower).replace(0, float("nan"))
        pct_b = (df["close"] - lower) / band_width

        df["bb_upper"] = upper
        df["bb_mid"] = mid
        df["bb_lower"] = lower
        df["bb_pct_b"] = pct_b

        df["bollinger_signal"] = "neutral"
        df.loc[pct_b < 0, "bollinger_signal"] = "below_lower"
        df.loc[pct_b > 1, "bollinger_signal"] = "above_upper"

        # Score: 1 = at/below lower band (bullish mean-reversion), 0 = at/above upper
        df["bollinger_score"] = (1 - pct_b).clip(0, 1)

        return df
