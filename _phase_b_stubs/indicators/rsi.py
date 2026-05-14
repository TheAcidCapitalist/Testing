"""RSI — Relative Strength Index (Wilder smoothing)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from scanner.indicators._base import BaseIndicator


class RSI(BaseIndicator):
    """14-period RSI with Wilder smoothing.  See spec/indicators.md §1."""

    name = "rsi"

    def __init__(
        self,
        period: int = 14,
        overbought: float = 70.0,
        oversold: float = 30.0,
    ) -> None:
        self.period = period
        self.overbought = overbought
        self.oversold = oversold

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        close = df["close"].astype(float)
        delta = close.diff()

        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)

        # Wilder smoothing (equivalent to EMA with α=1/period)
        avg_gain = gain.ewm(alpha=1 / self.period, min_periods=self.period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / self.period, min_periods=self.period, adjust=False).mean()

        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))

        df["rsi_value"] = rsi

        df["rsi_signal"] = np.where(
            rsi <= self.oversold,
            "oversold",
            np.where(rsi >= self.overbought, "overbought", "neutral"),
        )

        # Score: 1 = deeply oversold (bullish), 0 = deeply overbought
        df["rsi_score"] = ((self.overbought - rsi) / (self.overbought - self.oversold)).clip(0, 1)

        return df
