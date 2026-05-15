"""Tests for the Box Breakout (Congestion / Box Breakout) indicator.

Primary validation: six synthetic fixtures, each exhibiting a specific named
pattern. No 2012 ground-truth coverage; synthetic cases are the sole automated
validation per spec §8.

Fixture catalogue:
  box_flat_then_breakout_up   — 25-bar tight box then bullish breakout
  box_flat_then_breakout_down — 25-bar tight box then bearish breakout
  box_false_poke              — bar high pokes above box, close stays inside → neutral
  box_too_short               — tight range < min_congestion_bars → never valid → neutral
  box_trending_no_box         — steady uptrend, no congestion ever forms → neutral
  box_recency_expired         — breakout_recency+2 bars before end → direction expired to neutral

All use default params (min_congestion_bars=15, max_range=0.06, range_metric="pct",
breakout_buffer=0.25, breakout_recency=3) unless explicitly stated.

signal_value contract (spec §8):
  fresh bullish breakout → 0.25
  fresh bearish breakout → 0.75
  no fresh breakout      → 0.50
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from scanner.indicators import box_breakout as bb

FIXTURES_DIR = Path(__file__).parent / "fixtures"
TSC_DIR = FIXTURES_DIR / "tsc_2012"
SYN_DIR = FIXTURES_DIR / "synthetic"


def _syn(name: str) -> pd.DataFrame:
    return pd.read_csv(SYN_DIR / f"box_{name}.csv", parse_dates=["date"])


def _tsc(ticker: str) -> pd.DataFrame:
    df = pd.read_csv(TSC_DIR / f"{ticker}_ohlcv.csv", parse_dates=["date"])
    return df.iloc[::-1].reset_index(drop=True)


# ---------------------------------------------------------------------------
# 1. flat_then_breakout_up
# ---------------------------------------------------------------------------

def test_flat_then_breakout_up_direction() -> None:
    """25-bar tight box then bullish breakout → direction=buy, signal_value=0.25."""
    df = _syn("flat_then_breakout_up")
    r = bb.compute(df)
    assert r["direction"] == "buy", f"direction={r['direction']!r}"
    assert r["signal_value"] == 0.25, f"signal_value={r['signal_value']}"


def test_flat_then_breakout_up_box_metrics() -> None:
    """Box length must be exactly 25; days_since_breakout=0 on the breakout bar."""
    df = _syn("flat_then_breakout_up")
    r = bb.compute(df)
    assert r["box_length"] == 25, f"box_length={r['box_length']}"
    assert r["days_since_breakout"] == 0, f"days_since_breakout={r['days_since_breakout']}"


# ---------------------------------------------------------------------------
# 2. flat_then_breakout_down
# ---------------------------------------------------------------------------

def test_flat_then_breakout_down_direction() -> None:
    """25-bar tight box then bearish breakout → direction=sell, signal_value=0.75."""
    df = _syn("flat_then_breakout_down")
    r = bb.compute(df)
    assert r["direction"] == "sell", f"direction={r['direction']!r}"
    assert r["signal_value"] == 0.75, f"signal_value={r['signal_value']}"


def test_flat_then_breakout_down_box_metrics() -> None:
    """Box length exactly 25; days_since_breakout=0."""
    df = _syn("flat_then_breakout_down")
    r = bb.compute(df)
    assert r["box_length"] == 25, f"box_length={r['box_length']}"
    assert r["days_since_breakout"] == 0, f"days_since_breakout={r['days_since_breakout']}"


# ---------------------------------------------------------------------------
# 3. false_poke
# ---------------------------------------------------------------------------

def test_false_poke_neutral() -> None:
    """High pokes above box, close inside → no breakout, direction=neutral."""
    df = _syn("false_poke")
    r = bb.compute(df)
    assert r["direction"] == "neutral", f"direction={r['direction']!r}"
    assert r["signal_value"] == 0.5, f"signal_value={r['signal_value']}"
    assert r["days_since_breakout"] is None, (
        f"no breakout occurred, days_since_breakout should be None, got {r['days_since_breakout']}"
    )


# ---------------------------------------------------------------------------
# 4. too_short
# ---------------------------------------------------------------------------

def test_too_short_neutral() -> None:
    """8-bar tight phase < min_congestion_bars=15 → no valid box → neutral."""
    df = _syn("too_short")
    r = bb.compute(df)
    assert r["direction"] == "neutral", f"direction={r['direction']!r}"
    assert r["days_since_breakout"] is None, (
        f"too-short run never fires a breakout; days_since_breakout={r['days_since_breakout']}"
    )


# ---------------------------------------------------------------------------
# 5. trending_no_box
# ---------------------------------------------------------------------------

def test_trending_no_box_neutral() -> None:
    """Steady uptrend — no run ever reaches min_congestion_bars → neutral throughout."""
    df = _syn("trending_no_box")
    r = bb.compute(df)
    assert r["direction"] == "neutral", f"direction={r['direction']!r}"
    # No valid box ever formed
    assert r["box_high"] is None or (
        r["box_high"] is not None and r["days_since_breakout"] is None
    ), "trending series should produce no completed valid box"


def test_trending_no_box_series_never_active() -> None:
    """box_active must be False for every bar in the trending series."""
    df = _syn("trending_no_box")
    series = bb.compute_series(df)
    assert not series["box_active"].any(), (
        f"Expected no active box in trending series; "
        f"active bars: {series.index[series['box_active']].tolist()}"
    )


# ---------------------------------------------------------------------------
# 6. recency_expired
# ---------------------------------------------------------------------------

def test_recency_expired_direction() -> None:
    """Breakout breakout_recency+2 bars before end → direction expired to neutral."""
    df = _syn("recency_expired")
    r = bb.compute(df)
    assert r["direction"] == "neutral", (
        f"direction={r['direction']!r} — breakout should be stale (recency expired)"
    )


def test_recency_expired_days_since() -> None:
    """days_since_breakout must equal breakout_recency+2 = 5 at the last bar."""
    df = _syn("recency_expired")
    r = bb.compute(df)
    # Fixture: 25 tight bars + 1 breakout bar + 5 tail bars = 31 total.
    # Breakout at index 25, last bar at index 30 → days_since = 5.
    assert r["days_since_breakout"] == 5, (
        f"days_since_breakout={r['days_since_breakout']}, expected 5"
    )


# ---------------------------------------------------------------------------
# Recency window tests using compute_series on breakout_up fixture
# ---------------------------------------------------------------------------

def test_recency_window_in_series() -> None:
    """In the flat_then_breakout_up fixture, direction='buy' for the breakout bar
    and for the next breakout_recency=3 bars, then expires to 'neutral'."""
    # Add extra bars beyond recency to the fixture so we can test expiry.
    df_base = _syn("flat_then_breakout_up")
    # Append breakout_recency+2 = 5 neutral bars (high=102, low=98, close=100).
    extra_rows = pd.DataFrame({
        "date": pd.date_range(df_base["date"].iloc[-1], periods=6, freq="B")[1:],
        "open": [100.0] * 5, "high": [102.0] * 5, "low": [98.0] * 5,
        "close": [100.0] * 5, "volume": [1e6] * 5,
    })
    df_ext = pd.concat([df_base, extra_rows], ignore_index=True)
    series = bb.compute_series(df_ext)

    breakout_idx = 25  # bar where breakout occurs
    # bars 25, 26, 27, 28 are within recency (days_since 0, 1, 2, 3)
    for offset in range(4):
        bar = breakout_idx + offset
        assert series["direction"].iloc[bar] == "buy", (
            f"bar {bar} (offset {offset}) should still be 'buy'; "
            f"got {series['direction'].iloc[bar]!r}"
        )
    # bar 29: days_since=4 > breakout_recency=3 → neutral
    assert series["direction"].iloc[breakout_idx + 4] == "neutral", (
        f"bar {breakout_idx+4} should be 'neutral' (recency expired)"
    )


# ---------------------------------------------------------------------------
# Output contract
# ---------------------------------------------------------------------------

def test_output_keys_and_types() -> None:
    """compute() must return all required keys with correct types."""
    df = _tsc("WTI")
    r = bb.compute(df)
    assert "signal_value" in r and isinstance(r["signal_value"], float)
    assert "direction" in r and r["direction"] in {"buy", "sell", "neutral"}
    assert "box_high" in r
    assert "box_low" in r
    assert "box_length" in r
    assert "days_since_breakout" in r
    assert 0.0 <= r["signal_value"] <= 1.0


@pytest.mark.parametrize("ticker", ["WTI", "GOLD", "EUR", "JPY", "GBP"])
def test_signal_value_range_on_tsc(ticker: str) -> None:
    """signal_value must be in {0.25, 0.5, 0.75} (the three valid values)."""
    df = _tsc(ticker)
    r = bb.compute(df)
    assert r["signal_value"] in {0.25, 0.5, 0.75}, (
        f"{ticker}: signal_value={r['signal_value']} not in {{0.25, 0.5, 0.75}}"
    )


# ---------------------------------------------------------------------------
# Consistency: compute() == last row of compute_series()
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ticker", ["WTI", "GOLD", "EUR", "JPY", "GBP"])
def test_compute_series_consistency(ticker: str) -> None:
    """Last row of compute_series() must match compute() on all fields."""
    df = _tsc(ticker)
    latest = bb.compute(df)
    series = bb.compute_series(df)
    last = series.iloc[-1]

    assert str(last["direction"]) == latest["direction"], (
        f"{ticker}: series direction {last['direction']!r} != compute {latest['direction']!r}"
    )
    assert float(last["signal_value"]) == latest["signal_value"], (
        f"{ticker}: signal_value mismatch"
    )
