"""Tests for the Volatility confirmation indicator.

This is a confirmation indicator — it emits {percentile, state} not {signal_value, direction}.

Four test groups:
  1. Fixture test — reproduce vol_percentile in expected_indicators.csv for all 5 tickers.
     Uses the realized_vol column from the fixture CSV (Bloomberg RV).
  2. State-logic test — synthetic RV series verifying confirm / neutral / reject zones,
     including the reject state not covered by the 2012 fixtures.
  3. Short-history test — series shorter than `history` degrades gracefully (no error).
  4. Consistency test — last row of compute_series matches compute() latest-bar result.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from scanner.indicators import volatility as vol_mod

FIXTURES_DIR = Path(__file__).parent / "fixtures"
TSC_DIR = FIXTURES_DIR / "tsc_2012"
SYN_DIR = FIXTURES_DIR / "synthetic"

ABS_TOL = 1e-3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_tsc(ticker: str) -> pd.DataFrame:
    """Load TSC OHLCV (newest-first), reverse to chronological, keep realized_vol."""
    df = pd.read_csv(TSC_DIR / f"{ticker}_ohlcv.csv", parse_dates=["date"])
    return df.iloc[::-1].reset_index(drop=True)


def _load_syn(filename: str) -> pd.DataFrame:
    return pd.read_csv(SYN_DIR / filename, parse_dates=["date"])


# ---------------------------------------------------------------------------
# 1. Fixture test — vol_percentile against 2012 ground truth
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ticker,exp_state", [
    ("WTI",  "neutral"),   # 0.508 — between 0.3 and 0.7
    ("GOLD", "confirm"),   # 0.000 — below 0.3
    ("EUR",  "neutral"),   # 0.307 — between 0.3 and 0.7
    ("JPY",  "neutral"),   # 0.351 — between 0.3 and 0.7
    ("GBP",  "confirm"),   # 0.111 — below 0.3
])
def test_fixture_volatility(ticker: str, exp_state: str) -> None:
    """compute() on full TSC history (using Bloomberg realized_vol) must match
    expected_indicators.csv vol_percentile within abs_tol=1e-3, and state must be
    consistent with the returned percentile."""
    df = _load_tsc(ticker)
    expected = pd.read_csv(TSC_DIR / "expected_indicators.csv")
    row = expected[expected["short_name"] == ticker].iloc[0]

    result = vol_mod.compute(df)

    assert abs(result["percentile"] - row["vol_percentile"]) < ABS_TOL, (
        f"{ticker}: percentile {result['percentile']:.6f} vs expected {row['vol_percentile']:.6f}"
    )
    # State must be consistent with the returned percentile
    assert result["state"] == exp_state, (
        f"{ticker}: state '{result['state']}' but percentile {result['percentile']:.4f} "
        f"implies '{exp_state}'"
    )


# ---------------------------------------------------------------------------
# 2. State-logic test — synthetic RV series (confirm / neutral / reject)
# ---------------------------------------------------------------------------

def test_synthetic_confirm_state() -> None:
    """Low-percentile RV series (≈0.08) → state='confirm'."""
    df = _load_syn("vol_low_pct.csv")
    result = vol_mod.compute(df)
    assert result["percentile"] < 0.3, f"percentile={result['percentile']:.4f} not < 0.3"
    assert result["state"] == "confirm", f"state={result['state']!r}, expected 'confirm'"


def test_synthetic_neutral_state() -> None:
    """Mid-percentile RV series (≈0.50) → state='neutral'."""
    df = _load_syn("vol_mid_pct.csv")
    result = vol_mod.compute(df)
    assert 0.3 <= result["percentile"] <= 0.7, f"percentile={result['percentile']:.4f} not in [0.3, 0.7]"
    assert result["state"] == "neutral", f"state={result['state']!r}, expected 'neutral'"


def test_synthetic_reject_state() -> None:
    """High-percentile RV series (≈0.94) → state='reject'."""
    df = _load_syn("vol_high_pct.csv")
    result = vol_mod.compute(df)
    assert result["percentile"] > 0.7, f"percentile={result['percentile']:.4f} not > 0.7"
    assert result["state"] == "reject", f"state={result['state']!r}, expected 'reject'"


# ---------------------------------------------------------------------------
# 3. Short-history test — fewer bars than history degrades gracefully
# ---------------------------------------------------------------------------

def test_short_history_no_error() -> None:
    """A series shorter than history=180 must return a valid result, not raise."""
    df = _load_syn("vol_short.csv")   # 30 bars only
    result = vol_mod.compute(df)

    assert isinstance(result["percentile"], float), "percentile must be a float"
    assert 0.0 <= result["percentile"] <= 1.0, f"percentile={result['percentile']} out of [0,1]"
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
