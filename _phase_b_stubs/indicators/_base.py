"""Base class for all indicators."""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class BaseIndicator(ABC):
    """
    Contract every indicator must satisfy.

    Subclasses MUST:
    - Set a class-level ``name`` attribute (snake_case, unique across the package).
    - Implement ``compute(df)`` which accepts a standard OHLCV DataFrame and
      returns the same DataFrame with one or more new columns appended.

    Columns added by ``compute`` must include at minimum:
    - ``{name}_score``   — float in [0, 1]; higher = more bullish signal
    - ``{name}_signal``  — human-readable label (str)
    """

    name: str  # subclasses override

    @abstractmethod
    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Parameters
        ----------
        df:
            DataFrame with columns: date, open, high, low, close, volume.
            Sorted ascending by date.  Index is a RangeIndex or DatetimeIndex.

        Returns
        -------
        The same DataFrame with indicator columns appended in-place.
        """
        ...
