"""pytest configuration and shared fixtures."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"
TSC_DIR = FIXTURES_DIR / "tsc_2012"

# The five tickers present in the 2012 fixture set
TSC_TICKERS = ["WTI", "GOLD", "EUR", "JPY", "GBP"]


# ---------------------------------------------------------------------------
# Synthetic OHLCV — fast unit tests that don't need real data
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_ohlcv() -> pd.DataFrame:
    """260-row synthetic OHLCV DataFrame for indicator unit tests."""
    import numpy as np

    rng = np.random.default_rng(42)
    n = 260
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    close = np.clip(close, 1, None)

    return pd.DataFrame(
        {
            "date": pd.date_range("2023-01-01", periods=n, freq="B"),
            "open": close * rng.uniform(0.99, 1.01, n),
            "high": close * rng.uniform(1.00, 1.02, n),
            "low": close * rng.uniform(0.98, 1.00, n),
            "close": close,
            "volume": rng.integers(500_000, 5_000_000, n),
        }
    )


# ---------------------------------------------------------------------------
# TSC-2012 fixtures — extracted from TSC Macro Dashboard 31 May 2012.xlsm
# ---------------------------------------------------------------------------

def _load_ohlcv(ticker: str) -> pd.DataFrame:
    """Load one ticker's OHLCV CSV and reverse to chronological order.

    The spreadsheet stores rows newest-first; indicators expect oldest-first.
    """
    csv = TSC_DIR / f"{ticker}_ohlcv.csv"
    if not csv.exists():
        pytest.skip(f"Fixture not found: {csv}")
    df = pd.read_csv(csv, parse_dates=["date"])
    df = df.iloc[::-1].reset_index(drop=True)  # newest-first → chronological
    return df


@pytest.fixture(params=TSC_TICKERS)
def tsc_ticker(request) -> str:  # type: ignore[type-arg]
    """Parametrized fixture — yields each of the 5 TSC-2012 ticker names."""
    return request.param  # type: ignore[no-any-return]


@pytest.fixture(params=TSC_TICKERS)
def tsc_ohlcv(request) -> pd.DataFrame:
    """Parametrized fixture — loads chronological OHLCV for each TSC-2012 ticker."""
    return _load_ohlcv(request.param)


@pytest.fixture
def tsc_expected() -> pd.DataFrame:
    """Load expected_indicators.csv — one row per ticker, latest-bar values."""
    csv = TSC_DIR / "expected_indicators.csv"
    if not csv.exists():
        pytest.skip(f"Fixture not found: {csv}")
    return pd.read_csv(csv)


def tsc_ohlcv_for(ticker: str) -> pd.DataFrame:
    """Helper for tests that need a specific ticker's OHLCV (non-fixture form)."""
    return _load_ohlcv(ticker)

