"""Tests for the RSI indicator.

Three test groups:
  1. Fixture test — reproduce expected_indicators.csv for all 5 TSC-2012 tickers.
  2. Synthetic test — verify buy/sell/neutral direction from hand-built cross scenarios.
  3. Consistency test — last row of compute_series matches compute() output.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from scanner.indicators import rsi as rsi_mod

FIXTURES_DIR = Path(__file__).parent / "fixtures"
TSC_DIR = FIXTURES_DIR / "tsc_2012"
SYN_DIR = FIXTURES_DIR / "synthetic"

ABS_TOL = 1e-3

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_tsc(ticker: str) -> pd.DataFrame:
    """Load TSC OHLCV (newest-first) and reverse to chronological order."""
    df = pd.read_csv(TSC_DIR / f"{ticker}_ohlcv.csv", parse_dates=["date"])
    return df.iloc[::-1].reset_index(drop=True)


def _load_syn(filename: str) -> pd.DataFrame:
    """Load a synthetic fixture CSV in chronological order (already ascending)."""
    return pd.read_csv(SYN_DIR / filename, parse_dates=["date"])


# ---------------------------------------------------------------------------
# 1. Fixture test — 2012 ground truth
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ticker", ["WTI", "GOLD", "EUR", "JPY", "GBP"])
def test_fixture_rsi(ticker: str) -> None:
    """compute() on the full TSC history must match expected_indicators.csv."""
    df = _load_tsc(ticker)
    expected = pd.read_csv(TSC_DIR / "expected_indicators.csv")
    row = expected[expected["short_name"] == ticker].iloc[0]

    result = rsi_mod.compute(df)

    assert abs(result["rsi"] - row["rsi"]) < ABS_TOL, (
        f"{ticker}: rsi {result['rsi']:.6f} vs expected {row['rsi']:.6f}"
    )
    assert abs(result["rsi_prev"] - row["rsi_prev"]) < ABS_TOL, (
        f"{ticker}: rsi_prev {result['rsi_prev']:.6f} vs expected {row['rsi_prev']:.6f}"
    )
    assert result["rsi_flag"] == int(row["rsi_flag"]), (
        f"{ticker}: rsi_flag {result['rsi_flag']} vs expected {int(row['rsi_flag'])}"
    )


# ---------------------------------------------------------------------------
# 2. Synthetic tests — buy / sell / neutral signal logic
# ---------------------------------------------------------------------------

def test_synthetic_buy_cross() -> None:
    """RSI crossing UP through buy_cross threshold → direction='buy'."""
    df = _load_syn("rsi_buy_cross.csv")
    result = rsi_mod.compute(df)
    assert result["direction"] == "buy", (
        f"Expected 'buy', got '{result['direction']}' "
        f"(rsi={result['rsi']:.3f}, rsi_prev={result['rsi_prev']:.3f})"
    )


def test_synthetic_sell_cross() -> None:
    """RSI crossing DOWN through sell_cross threshold → direction='sell'."""
    df = _load_syn("rsi_sell_cross.csv")
    result = rsi_mod.compute(df)
    assert result["direction"] == "sell", (
        f"Expected 'sell', got '{result['direction']}' "
        f"(rsi={result['rsi']:.3f}, rsi_prev={result['rsi_prev']:.3f})"
    )


def test_synthetic_neutral() -> None:
    """RSI staying mid-range with no threshold cross → direction='neutral'."""
    df = _load_syn("rsi_neutral.csv")
    result = rsi_mod.compute(df)
    assert result["direction"] == "neutral", (
        f"Expected 'neutral', got '{result['direction']}' "
        f"(rsi={result['rsi']:.3f}, rsi_prev={result['rsi_prev']:.3f})"
    )


# ---------------------------------------------------------------------------
# 3. Consistency test — compute_series last row matches compute()
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ticker", ["WTI", "GOLD", "EUR", "JPY", "GBP"])
def test_compute_series_consistency(ticker: str) -> None:
    """Last row of compute_series must agree with compute() on rsi and direction."""
    df = _load_tsc(ticker)

    latest = rsi_mod.compute(df)
    series = rsi_mod.compute_series(df)
    last = series.iloc[-1]

    assert abs(float(last["rsi"]) - latest["rsi"]) < ABS_TOL, (
        f"{ticker}: series rsi {float(last['rsi']):.6f} != compute rsi {latest['rsi']:.6f}"
    )
    assert str(last["direction"]) == latest["direction"], (
        f"{ticker}: series direction {last['direction']!r} != compute direction {latest['direction']!r}"
    )
