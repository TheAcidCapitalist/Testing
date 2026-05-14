"""Volume — relative volume ratio score."""

from __future__ import annotations

import numpy as np
import pandas as pd

from scanner.indicators._base import BaseIndicator


class Volume(BaseIndicator):
    """Volume vs 20-day SMA ratio.  See spec/indicators.md §7."""

    name = "volume"

    def __init__(self, period: int = 20) -> None:
        self.period = period

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        avg_vol = df["volume"].rolling(self.period).mean()
        vol_ratio = df["volume"] / avg_vol.replace(0, np.nan)

        df["volume_sma"] = avg_vol
        df["volume_ratio"] = vol_ratio

        df["volume_signal"] = np.where(
            vol_ratio >= 2.0, "high", np.where(vol_ratio <= 0.5, "low", "normal")
        )

        # Score: clamp((ratio − 0.5) / 1.5, 0, 1) → 1 at 2× avg
        df["volume_score"] = ((vol_ratio - 0.5) / 1.5).clip(0, 1)

        return df
