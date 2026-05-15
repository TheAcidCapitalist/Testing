"""Tests for the Stochastic Oscillator with divergence (trade indicator).

Three layers tested:
  1. %K / %D numerical accuracy — validated against pandas-ta.
  2. Divergence + combined signal — 5 synthetic fixtures.
  3. Consistency — compute_series last row matches compute().

No 2012 fixture coverage (stochastic has no column in expected_indicators.csv).
Divergence is validated by synthetic fixtures with known outcomes only.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from scanner.indicators import stochastic as stoch_mod
from scanner.indicators._stochastic_core import stochastic_d, stochastic_k

FIXTURES_DIR = Path(__file__).parent / "fixtures"
TSC_DIR = FIXTURES_DIR / "tsc_2012"
SYN_DIR = FIXTURES_DIR / "synthetic"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_tsc(ticker: str) -> pd.DataFrame:
    df = pd.read_csv(TSC_DIR / f"{ticker}_ohlcv.csv", parse_dates=["date"])
    return df.iloc[::-1].reset_index(drop=True)


def _load_syn(filename: str) -> pd.DataFrame:
    return pd.read_csv(SYN_DIR / filename, parse_dates=["date"])


# ---------------------------------------------------------------------------
# 1. Layer 1: %K / %D numerical test
#    Validate against pandas-ta stoch(k=14, d=5, smooth_k=1) on real TSC data.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ticker", ["WTI", "GOLD", "EUR", "JPY", "GBP"])
def test_k_d_matches_pandas_ta(ticker: str) -> None:
    """_stochastic_core.stochastic_k / stochastic_d must match pandas-ta."""
    try:
        import pandas_ta as ta  # type: ignore[import-untyped]
    except ImportError:
        pytest.skip("pandas-ta not installed")

    df = _load_tsc(ticker)

    my_k = stochastic_k(df, k_days=14)
    my_d = stochastic_d(my_k, d_days=5)

    ref = ta.stoch(df["high"], df["low"], df["close"], k=14, d=5, smooth_k=1)
    # pandas-ta column names: STOCHk_14_5_1, STOCHd_14_5_1
    ref_k = ref.iloc[:, 0].reset_index(drop=True)
    ref_d = ref.iloc[:, 1].reset_index(drop=True)

    mask = ref_k.notna() & my_k.notna()
    diff_k = (my_k[mask] - ref_k[mask]).abs().max()
    diff_d = (my_d[mask] - ref_d[mask]).abs().max()

    assert diff_k < 1e-6, f"{ticker}: max K diff {diff_k:.2e} vs pandas-ta"
    assert diff_d < 1e-6, f"{ticker}: max D diff {diff_d:.2e} vs pandas-ta"


# ---------------------------------------------------------------------------
# 2. Divergence + signal tests — 5 synthetic fixtures
# ---------------------------------------------------------------------------

def test_bullish_divergence_fires() -> None:
    """Bullish divergence fixture: price lower lows + stoch higher lows + K<20 → buy."""
    df = _load_syn("stoch_bullish_div.csv")
    result = stoch_mod.compute(df)
    assert result["direction"] == "buy", (
        f"Expected 'buy', got '{result['direction']}'. "
        f"K={result['stoch_k']:.2f}, signal_value={result['signal_value']:.4f}"
    )


def test_bearish_divergence_fires() -> None:
    """Bearish divergence fixture: price higher highs + stoch lower highs + K>80 → sell."""
    df = _load_syn("stoch_bearish_div.csv")
    result = stoch_mod.compute(df)
    assert result["direction"] == "sell", (
        f"Expected 'sell', got '{result['direction']}'. "
        f"K={result['stoch_k']:.2f}, signal_value={result['signal_value']:.4f}"
    )


def test_threshold_no_divergence_neutral() -> None:
    """K<20 but no price/stoch divergence → neutral (threshold alone does not fire)."""
    df = _load_syn("stoch_threshold_no_div.csv")
    result = stoch_mod.compute(df)
    assert result["direction"] == "neutral", (
        f"Expected 'neutral', got '{result['direction']}'. "
        f"K={result['stoch_k']:.2f}"
    )


def test_divergence_no_threshold_neutral() -> None:
    """Divergence pattern present but K between 20-80 → neutral (both conditions required)."""
    df = _load_syn("stoch_div_no_threshold.csv")
    result = stoch_mod.compute(df)
    assert result["direction"] == "neutral", (
        f"Expected 'neutral', got '{result['direction']}'. "
        f"K={result['stoch_k']:.2f}"
    )


def test_no_pivot_neutral() -> None:
    """Fewer than 2 pivots of same type → no divergence detectable → neutral."""
    df = _load_syn("stoch_no_pivot.csv")
    result = stoch_mod.compute(df)
    assert result["direction"] == "neutral", (
        f"Expected 'neutral', got '{result['direction']}'. "
        f"K={result['stoch_k']:.2f}"
    )


# ---------------------------------------------------------------------------
# 3. Output contract
# ---------------------------------------------------------------------------

def test_output_keys_present() -> None:
    """compute() must return signal_value, direction, stoch_k, stoch_d."""
    df = _load_tsc("WTI")
    result = stoch_mod.compute(df)
    assert "signal_value" in result
    assert "direction" in result
    assert "stoch_k" in result
    assert "stoch_d" in result
    assert result["direction"] in {"buy", "sell", "neutral"}
    assert 0.0 <= result["signal_value"] <= 1.0


def test_signal_value_range() -> None:
    """signal_value must be in [0, 1] for all fixtures."""
    for ticker in ["WTI", "GOLD", "EUR", "JPY", "GBP"]:
        df = _load_tsc(ticker)
        r = stoch_mod.compute(df)
        assert 0.0 <= r["signal_value"] <= 1.0, (
            f"{ticker}: signal_value {r['signal_value']:.4f} out of [0, 1]"
        )


# ---------------------------------------------------------------------------
# 4. Consistency: compute_series last row matches compute()
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ticker", ["WTI", "GOLD", "EUR", "JPY", "GBP"])
def test_compute_series_consistency(ticker: str) -> None:
    """Last row of compute_series must agree with compute() on all fields."""
    df = _load_tsc(ticker)
    latest = stoch_mod.compute(df)
    series = stoch_mod.compute_series(df)
    last = series.iloc[-1]

    assert abs(float(last["stoch_k"]) - latest["stoch_k"]) < 1e-6, (
        f"{ticker}: series K {float(last['stoch_k']):.6f} != compute K {latest['stoch_k']:.6f}"
    )
    assert abs(float(last["stoch_d"]) - latest["stoch_d"]) < 1e-6, (
        f"{ticker}: series D {float(last['stoch_d']):.6f} != compute D {latest['stoch_d']:.6f}"
    )
    assert str(last["direction"]) == latest["direction"], (
        f"{ticker}: series direction {last['direction']!r} != compute direction {latest['direction']!r}"
    )
