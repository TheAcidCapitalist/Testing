"""Tests for the Daily Trend Divergence and Contrarian indicators.

Three test groups:
  1. Fixture test — reproduce expected_indicators.csv (daily_trend, daily_trend_prev,
     dt_flag) for the Divergence indicator on all 5 TSC-2012 tickers.
  2. Synthetic test — verify that each indicator fires at exactly the correct crossing
     bar and stays neutral everywhere else.  Four crossing scenarios:
       dt_div_buy  — slope rises through +0.005  → Divergence=buy,  Contrarian=neutral
       dt_div_sell — slope falls through −0.005  → Divergence=sell, Contrarian=neutral
       dt_con_buy  — slope rises through −0.005  → Contrarian=buy,  Divergence=neutral
       dt_con_sell — slope falls through +0.005  → Contrarian=sell, Divergence=neutral
     Plus a flat control (slope ≈ 0.001) where both are neutral throughout.
  3. Consistency test — last row of compute_series matches compute() for both indicators.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from scanner.indicators import daily_trend_contrarian as dtc_mod
from scanner.indicators import daily_trend_divergence as dtd_mod

FIXTURES_DIR = Path(__file__).parent / "fixtures"
TSC_DIR = FIXTURES_DIR / "tsc_2012"
SYN_DIR = FIXTURES_DIR / "synthetic"

ABS_TOL = 1e-3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_tsc(ticker: str) -> pd.DataFrame:
    df = pd.read_csv(TSC_DIR / f"{ticker}_ohlcv.csv", parse_dates=["date"])
    return df.iloc[::-1].reset_index(drop=True)


def _load_syn(filename: str) -> pd.DataFrame:
    return pd.read_csv(SYN_DIR / filename, parse_dates=["date"])


# ---------------------------------------------------------------------------
# 1. Fixture test — Divergence against 2012 ground truth
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ticker", ["WTI", "GOLD", "EUR", "JPY", "GBP"])
def test_fixture_divergence(ticker: str) -> None:
    """Divergence compute() must match daily_trend, daily_trend_prev, dt_flag."""
    df = _load_tsc(ticker)
    expected = pd.read_csv(TSC_DIR / "expected_indicators.csv")
    row = expected[expected["short_name"] == ticker].iloc[0]

    result = dtd_mod.compute(df)

    assert abs(result["daily_trend"] - row["daily_trend"]) < ABS_TOL, (
        f"{ticker}: daily_trend {result['daily_trend']:.8f} vs expected {row['daily_trend']:.8f}"
    )
    assert abs(result["daily_trend_prev"] - row["daily_trend_prev"]) < ABS_TOL, (
        f"{ticker}: daily_trend_prev {result['daily_trend_prev']:.8f} vs expected {row['daily_trend_prev']:.8f}"
    )
    assert result["dt_flag"] == int(row["dt_flag"]), (
        f"{ticker}: dt_flag {result['dt_flag']} vs expected {int(row['dt_flag'])}"
    )


# ---------------------------------------------------------------------------
# 2. Synthetic tests — cross logic, one crossing per series
# ---------------------------------------------------------------------------

def _assert_exactly_one_signal(series: pd.DataFrame, direction: str, name: str) -> None:
    """Assert exactly one bar fires the given direction; all others are neutral."""
    signal_bars = series[series["direction"] == direction]
    assert len(signal_bars) == 1, (
        f"{name}: expected exactly 1 '{direction}' bar, got {len(signal_bars)}"
    )


def _assert_all_neutral(series: pd.DataFrame, name: str) -> None:
    """Assert every bar in the series is neutral."""
    non_neutral = series[series["direction"] != "neutral"]
    assert len(non_neutral) == 0, (
        f"{name}: expected all neutral, got {len(non_neutral)} non-neutral bar(s) "
        f"at indices {non_neutral.index.tolist()}"
    )


def test_synthetic_divergence_buy() -> None:
    """Slope rises through +0.005: Divergence fires exactly one buy, Contrarian stays neutral."""
    df = _load_syn("dt_div_buy.csv")
    series_div = dtd_mod.compute_series(df)
    series_con = dtc_mod.compute_series(df)

    _assert_exactly_one_signal(series_div, "buy", "Divergence on dt_div_buy")
    _assert_all_neutral(series_con, "Contrarian on dt_div_buy")


def test_synthetic_divergence_sell() -> None:
    """Slope falls through −0.005: Divergence fires exactly one sell, Contrarian stays neutral."""
    df = _load_syn("dt_div_sell.csv")
    series_div = dtd_mod.compute_series(df)
    series_con = dtc_mod.compute_series(df)

    _assert_exactly_one_signal(series_div, "sell", "Divergence on dt_div_sell")
    _assert_all_neutral(series_con, "Contrarian on dt_div_sell")


def test_synthetic_contrarian_buy() -> None:
    """Slope rises through −0.005: Contrarian fires exactly one buy, Divergence stays neutral."""
    df = _load_syn("dt_con_buy.csv")
    series_div = dtd_mod.compute_series(df)
    series_con = dtc_mod.compute_series(df)

    _assert_all_neutral(series_div, "Divergence on dt_con_buy")
    _assert_exactly_one_signal(series_con, "buy", "Contrarian on dt_con_buy")


def test_synthetic_contrarian_sell() -> None:
    """Slope falls through +0.005: Contrarian fires exactly one sell, Divergence stays neutral."""
    df = _load_syn("dt_con_sell.csv")
    series_div = dtd_mod.compute_series(df)
    series_con = dtc_mod.compute_series(df)

    _assert_all_neutral(series_div, "Divergence on dt_con_sell")
    _assert_exactly_one_signal(series_con, "sell", "Contrarian on dt_con_sell")


def test_synthetic_flat_both_neutral() -> None:
    """Slope stays near 0.001 throughout: both indicators are neutral on every bar."""
    df = _load_syn("dt_flat.csv")
    _assert_all_neutral(dtd_mod.compute_series(df), "Divergence on dt_flat")
    _assert_all_neutral(dtc_mod.compute_series(df), "Contrarian on dt_flat")


# ---------------------------------------------------------------------------
# 3. Consistency test — compute_series last row matches compute()
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ticker", ["WTI", "GOLD", "EUR", "JPY", "GBP"])
def test_compute_series_consistency_divergence(ticker: str) -> None:
    df = _load_tsc(ticker)
    latest = dtd_mod.compute(df)
    series = dtd_mod.compute_series(df)
    last = series.iloc[-1]

    assert abs(float(last["daily_trend"]) - latest["daily_trend"]) < ABS_TOL, (
        f"{ticker} Divergence: series slope {float(last['daily_trend']):.8f} "
        f"!= compute slope {latest['daily_trend']:.8f}"
    )
    assert str(last["direction"]) == latest["direction"], (
        f"{ticker} Divergence: series direction {last['direction']!r} "
        f"!= compute direction {latest['direction']!r}"
    )


@pytest.mark.parametrize("ticker", ["WTI", "GOLD", "EUR", "JPY", "GBP"])
def test_compute_series_consistency_contrarian(ticker: str) -> None:
    df = _load_tsc(ticker)
    latest = dtc_mod.compute(df)
    series = dtc_mod.compute_series(df)
    last = series.iloc[-1]

    assert abs(float(last["daily_trend"]) - latest["daily_trend"]) < ABS_TOL, (
        f"{ticker} Contrarian: series slope {float(last['daily_trend']):.8f} "
        f"!= compute slope {latest['daily_trend']:.8f}"
    )
    assert str(last["direction"]) == latest["direction"], (
        f"{ticker} Contrarian: series direction {last['direction']!r} "
        f"!= compute direction {latest['direction']!r}"
    )
