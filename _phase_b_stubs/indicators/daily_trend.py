"""Daily Trend — linear regression slope over a rolling window."""

from __future__ import annotations

import numpy as np
import pandas as pd

from scanner.indicators._base import BaseIndicator


class DailyTrend(BaseIndicator):
    """Linear regression trend classifier.  See spec/indicators.md §4."""

    name = "daily_trend"

    def __init__(self, period: int = 20, up_thresh: float = 0.01, down_thresh: float = -0.01) -> None:
        self.period = period
        self.up_thresh = up_thresh
        self.down_thresh = down_thresh

    def _slope(self, series: pd.Series) -> float:
        """Normalised OLS slope: Δclose-per-day / mean(close)."""
        y = series.values.astype(float)
        if np.isnan(y).any() or y.mean() == 0:
            return float("nan")
        x = np.arange(len(y))
        slope = np.polyfit(x, y, 1)[0]
        return slope / y.mean()

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        slope = (
            df["close"]
            .rolling(self.period)
            .apply(self._slope, raw=False)
        )

        df["daily_trend_slope"] = slope

        df["daily_trend_signal"] = np.where(
            slope > self.up_thresh,
            "up",
            np.where(slope < self.down_thresh, "down", "flat"),
        )

        df["daily_trend_score"] = np.where(
            slope > self.up_thresh,
            1.0,
            np.where(slope < self.down_thresh, 0.0, 0.5),
        )

        return df
