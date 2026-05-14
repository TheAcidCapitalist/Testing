"""Tests for the Volume confirmation indicator.

This is a confirmation indicator — it emits {percentile, state} not {signal_value, direction}.
High volume percentile = trend confirmed (opposite of Volatility).

Four test groups:
  1. Fixture test — reproduce volume_percentile in expected_indicators.csv for all 5 tickers.
     All five fixture tickers land in the reject zone (<0.3), so confirm and neutral states
     are validated by synthetic tests only.
  2. State-logic test — synthetic volume series verifying confirm (>0.7) / neutral / reject (<0.3).
  3. Short-history test — series shorter than history=180 degrades gracefully (no error).
  4. Consistency test — last row of compute_series matches compute() latest-bar result.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from scanner.indicators import volume as vol_mod

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
# 1. Fixture test — volume_percentile against 2012 ground truth
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ticker", ["WTI", "GOLD", "EUR", "JPY", "GBP"])
def test_fixture_volume(ticker: str) -> None:
    """compute() on full TSC history must match expected_indicators.csv volume_percentile
    within abs_tol=1e-3, and state must be consistent with the returned percentile."""
    df = _load_tsc(ticker)
    expected = pd.read_csv(TSC_DIR / "expected_indicators.csv")
    row = expected[expected["short_name"] == ticker].iloc[0]

    result = vol_mod.compute(df)

    assert abs(result["percentile"] - row["volume_percentile"]) < ABS_TOL, (
        f"{ticker}: percentile {result['percentile']:.6f} vs expected {row['volume_percentile']:.6f}"
    )
    # All fixture tickers have volume_percentile < 0.3 → reject
    assert result["state"] == "reject", (
        f"{ticker}: state '{result['state']}' but percentile {result['percentile']:.4f} < 0.3 → 'reject'"
    )


# ---------------------------------------------------------------------------
# 2. State-logic test — synthetic volume series (confirm / neutral / reject)
# ---------------------------------------------------------------------------

def test_synthetic_confirm_state() -> None:
    """High-percentile volume series (≈0.94) → state='confirm'."""
    df = _load_syn("volume_high_pct.csv")
    result = vol_mod.compute(df)
    assert result["percentile"] > 0.7, f"percentile={result['percentile']:.4f} not > 0.7"
    assert result["state"] == "confirm", f"state={result['state']!r}, expected 'confirm'"


def test_synthetic_neutral_state() -> None:
    """Mid-percentile volume series (≈0.50) → state='neutral'."""
    df = _load_syn("volume_mid_pct.csv")
    result = vol_mod.compute(df)
    assert 0.3 <= result["percentile"] <= 0.7, (
        f"percentile={result['percentile']:.4f} not in [0.3, 0.7]"
    )
    assert result["state"] == "neutral", f"state={result['state']!r}, expected 'neutral'"


def test_synthetic_reject_state() -> None:
    """Low-percentile volume series (≈0.08) → state='reject'."""
    df = _load_syn("volume_low_pct.csv")
    result = vol_mod.compute(df)
    assert result["percentile"] < 0.3, f"percentile={result['percentile']:.4f} not < 0.3"
    assert result["state"] == "reject", f"state={result['state']!r}, expected 'reject'"


# ---------------------------------------------------------------------------
# 3. Short-history test — fewer bars than history degrades gracefully
# ---------------------------------------------------------------------------

def test_short_history_no_error() -> None:
    """A series shorter than history=180 must return a valid result, not raise."""
    df = _load_syn("volume_short.csv")   # 30 bars only
    result = vol_mod.compute(df)

    assert isinstance(result["percentile"], float), "percentile must be a float"
    assert 0.0 <= result["percentile"] <= 1.0, f"percentile={result['percentile']} out of [0, 1]"
    assert result["state"] in {"confirm", "neutral", "reject"}, f"invalid state {result['state']!r}"


# ---------------------------------------------------------------------------
# 4. Consistency test — compute_series last row matches compute()
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ticker", ["WTI", "GOLD", "EUR", "JPY", "GBP"])
def test_compute_series_consistency(ticker: str) -> None:
    """Last row of compute_series must agree with compute() on percentile and state."""
    df = _load_tsc(ticker)
    latest = vol_mod.compute(df)
    series = vol_mod.compute_series(df)
    last = series.iloc[-1]

    assert abs(float(last["percentile"]) - latest["percentile"]) < ABS_TOL, (
        f"{ticker}: series pct {float(last['percentile']):.6f} != compute pct {latest['percentile']:.6f}"
    )
    assert str(last["state"]) == latest["state"], (
        f"{ticker}: series state {last['state']!r} != compute state {latest['state']!r}"
    )
