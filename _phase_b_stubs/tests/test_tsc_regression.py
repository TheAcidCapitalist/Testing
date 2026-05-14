"""Regression tests against TSC-2012 fixture data.

Validates that the indicator engine reproduces the values the original
spreadsheet computed for 5 macro tickers (WTI, GOLD, EUR, JPY, GBP) as of
2012-08-29.

Tolerances (from spec/indicators.md §Validation):
  - Continuous values: abs_tol = 1e-3
  - Discrete flags:    exact match
  - Day counters:      exact match
"""

from __future__ import annotations

import pandas as pd
import pytest

from tests.conftest import TSC_TICKERS, tsc_ohlcv_for
from scanner.indicators import REGISTRY
from scanner.scoring import compute_combo_score, rank_results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_all_indicators(ticker: str) -> pd.Series:
    """Load OHLCV for *ticker*, run all indicators, return the latest bar."""
    df = tsc_ohlcv_for(ticker)
    for cls in REGISTRY.values():
        df = cls().compute(df)
    return df.iloc[-1]


def _expected(ticker: str, tsc_expected: pd.DataFrame) -> pd.Series:
    row = tsc_expected[tsc_expected["short_name"] == ticker]
    assert not row.empty, f"No expected row for {ticker}"
    return row.iloc[0]


# ---------------------------------------------------------------------------
# RSI regression — abs_tol 1e-3, flag exact
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ticker", TSC_TICKERS)
def test_tsc_rsi_value(ticker: str, tsc_expected: pd.DataFrame) -> None:
    result = _run_all_indicators(ticker)
    expected = _expected(ticker, tsc_expected)
    assert abs(result["rsi_value"] - expected["rsi"]) < 1e-3, (
        f"{ticker}: rsi={result['rsi_value']:.6f}, expected={expected['rsi']:.6f}"
    )


# ---------------------------------------------------------------------------
# Daily Trend regression
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ticker", TSC_TICKERS)
def test_tsc_daily_trend_value(ticker: str, tsc_expected: pd.DataFrame) -> None:
    result = _run_all_indicators(ticker)
    expected = _expected(ticker, tsc_expected)
    assert abs(result["daily_trend_slope"] - expected["daily_trend"]) < 1e-3, (
        f"{ticker}: daily_trend={result['daily_trend_slope']:.6f}, "
        f"expected={expected['daily_trend']:.6f}"
    )


# ---------------------------------------------------------------------------
# Bollinger Z regression
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ticker", TSC_TICKERS)
def test_tsc_bollinger_z(ticker: str, tsc_expected: pd.DataFrame) -> None:
    result = _run_all_indicators(ticker)
    expected = _expected(ticker, tsc_expected)
    assert abs(result["bb_pct_b"] - expected["bollinger_z"]) < 1e-3, (
        f"{ticker}: bollinger={result['bb_pct_b']:.6f}, "
        f"expected={expected['bollinger_z']:.6f}"
    )


# ---------------------------------------------------------------------------
# Volatility percentile regression
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ticker", TSC_TICKERS)
def test_tsc_vol_percentile(ticker: str, tsc_expected: pd.DataFrame) -> None:
    result = _run_all_indicators(ticker)
    expected = _expected(ticker, tsc_expected)
    assert abs(result["vol_z"] - expected["vol_percentile"]) < 1e-3, (
        f"{ticker}: vol_z={result['vol_z']:.6f}, "
        f"expected={expected['vol_percentile']:.6f}"
    )


# ---------------------------------------------------------------------------
# Full pipeline smoke test — runs without error on all 5 tickers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ticker", TSC_TICKERS)
def test_tsc_full_pipeline_runs(ticker: str) -> None:
    """Indicator pipeline + scoring must complete without raising on real data."""
    df = tsc_ohlcv_for(ticker)
    for cls in REGISTRY.values():
        df = cls().compute(df)

    last = df.iloc[[-1]].copy()
    last["ticker"] = ticker
    last["exchange"] = "TSC_FIXTURE"
    scored = compute_combo_score(last)
    ranked = rank_results(scored)

    assert "combo_score" in ranked.columns or ranked.empty
