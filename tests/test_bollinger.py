"""Tests for Bollinger Band Normal and Contrarian indicators.

Three test groups:
  1. Fixture test — reproduce expected_indicators.csv (bollinger_z, bollinger_days)
     for the Normal indicator on all 5 TSC-2012 tickers.
  2. Synthetic test — verify Normal and Contrarian produce opposite directions
     on the same series (above-band → Normal=buy, Contrarian=sell; and vice versa).
  3. Consistency test — last row of compute_series matches compute() for both indicators.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from scanner.indicators import bollinger_contrarian as bc_mod
from scanner.indicators import bollinger_normal as bn_mod

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
# 1. Fixture test — Normal indicator against 2012 ground truth
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ticker", ["WTI", "GOLD", "EUR", "JPY", "GBP"])
def test_fixture_bollinger_normal(ticker: str) -> None:
    """compute() on the full TSC history must match expected_indicators.csv."""
    df = _load_tsc(ticker)
    expected = pd.read_csv(TSC_DIR / "expected_indicators.csv")
    row = expected[expected["short_name"] == ticker].iloc[0]

    result = bn_mod.compute(df)

    assert abs(result["bollinger_z"] - row["bollinger_z"]) < ABS_TOL, (
        f"{ticker}: bollinger_z {result['bollinger_z']:.6f} vs expected {row['bollinger_z']:.6f}"
    )
    assert result["bollinger_days"] == int(row["bollinger_days"]), (
        f"{ticker}: bollinger_days {result['bollinger_days']} vs expected {int(row['bollinger_days'])}"
    )
    # bollinger_time is an exit-timing field — not tested per spec


# ---------------------------------------------------------------------------
# 2. Synthetic tests — Normal and Contrarian produce opposite directions
# ---------------------------------------------------------------------------

def test_synthetic_above_band() -> None:
    """Price spike far above the band: Normal=buy, Contrarian=sell."""
    df = _load_syn("bollinger_above.csv")

    n = bn_mod.compute(df)
    c = bc_mod.compute(df)

    assert n["direction"] == "buy", (
        f"Normal expected 'buy', got '{n['direction']}' (z={n['bollinger_z']:.3f})"
    )
    assert c["direction"] == "sell", (
        f"Contrarian expected 'sell', got '{c['direction']}' (z={c['bollinger_z']:.3f})"
    )
    assert n["direction"] != c["direction"], "Normal and Contrarian must be opposite for above-band"


def test_synthetic_below_band() -> None:
    """Price spike far below the band: Normal=sell, Contrarian=buy."""
    df = _load_syn("bollinger_below.csv")

    n = bn_mod.compute(df)
    c = bc_mod.compute(df)

    assert n["direction"] == "sell", (
        f"Normal expected 'sell', got '{n['direction']}' (z={n['bollinger_z']:.3f})"
    )
    assert c["direction"] == "buy", (
        f"Contrarian expected 'buy', got '{c['direction']}' (z={c['bollinger_z']:.3f})"
    )
    assert n["direction"] != c["direction"], "Normal and Contrarian must be opposite for below-band"


def test_synthetic_inside_band() -> None:
    """Price mid-range: both Normal and Contrarian are neutral."""
    df = _load_syn("bollinger_inside.csv")

    n = bn_mod.compute(df)
    c = bc_mod.compute(df)

    assert n["direction"] == "neutral", (
        f"Normal expected 'neutral', got '{n['direction']}' (z={n['bollinger_z']:.3f})"
    )
    assert c["direction"] == "neutral", (
        f"Contrarian expected 'neutral', got '{c['direction']}' (z={c['bollinger_z']:.3f})"
    )


# ---------------------------------------------------------------------------
# 3. Consistency test — compute_series last row matches compute()
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ticker", ["WTI", "GOLD", "EUR", "JPY", "GBP"])
def test_compute_series_consistency_normal(ticker: str) -> None:
    """Last row of compute_series must agree with compute() on bollinger_z and direction."""
    df = _load_tsc(ticker)

    latest = bn_mod.compute(df)
    series = bn_mod.compute_series(df)
    last = series.iloc[-1]

    assert abs(float(last["bollinger_z"]) - latest["bollinger_z"]) < ABS_TOL, (
        f"{ticker} Normal: series z {float(last['bollinger_z']):.6f} != compute z {latest['bollinger_z']:.6f}"
    )
    assert str(last["direction"]) == latest["direction"], (
        f"{ticker} Normal: series direction {last['direction']!r} != compute direction {latest['direction']!r}"
    )


@pytest.mark.parametrize("ticker", ["WTI", "GOLD", "EUR", "JPY", "GBP"])
def test_compute_series_consistency_contrarian(ticker: str) -> None:
    """Last row of compute_series must agree with compute() on bollinger_z and direction."""
    df = _load_tsc(ticker)

    latest = bc_mod.compute(df)
    series = bc_mod.compute_series(df)
    last = series.iloc[-1]

    assert abs(float(last["bollinger_z"]) - latest["bollinger_z"]) < ABS_TOL, (
        f"{ticker} Contrarian: series z {float(last['bollinger_z']):.6f} != compute z {latest['bollinger_z']:.6f}"
    )
    assert str(last["direction"]) == latest["direction"], (
        f"{ticker} Contrarian: series direction {last['direction']!r} != compute direction {latest['direction']!r}"
    )
