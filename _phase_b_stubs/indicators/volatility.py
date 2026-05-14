"""Volatility — annualised vol Z-score."""

from __future__ import annotations

import numpy as np
import pandas as pd

from scanner.indicators._base import BaseIndicator


class Volatility(BaseIndicator):
    """Annualised realised vol vs its 1-year Z-score.  See spec/indicators.md §6."""

    name = "volatility"

    def __init__(self, period: int = 20, z_window: int = 252) -> None:
        self.period = period
        self.z_window = z_window

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        log_ret = np.log(df["close"] / df["close"].shift(1))
        ann_vol = log_ret.rolling(self.period).std() * np.sqrt(252)

        roll_mean = ann_vol.rolling(self.z_window).mean()
        roll_std = ann_vol.rolling(self.z_window).std()
        vol_z = (ann_vol - roll_mean) / roll_std.replace(0, np.nan)

        df["vol_ann"] = ann_vol
        df["vol_z"] = vol_z

        df["volatility_signal"] = "normal"
        df.loc[vol_z < -1, "volatility_signal"] = "low"
        df.loc[vol_z > 1, "volatility_signal"] = "high"

        # Score: 1 when vol is contractin (z < -1), 0 when elevated
        df["volatility_score"] = ((-vol_z - 1) / 2).clip(0, 1)

        return df
