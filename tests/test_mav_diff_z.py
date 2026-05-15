"""Tests for the MAV Difference Z-Score confirmation indicator.

No 2012 fixture: expected_indicators.csv has no MAV-diff-z column.
Validation splits into two parts:
  1. Numerical accuracy — z-score formula validated against an independent
     pandas reference computation on a known close series.
  2. Sign-change detection — synthetic CSV fixtures plus inline edge-case tests.

All tests use reduced parameters (mav1=3, mav2=5, z_history=4) so that the
warmup is only 7 bars and fixtures stay short.  The formulas and transition
logic are identical at any parameter size.

Warmup with mav1=3, mav2=5, z_history=4:
  mav2 + z_history − 2 = 5 + 4 − 2 = 7 bars.
  First z-score at bar index 7 (8th bar).

Zero-touch rule implemented and tested here:
  Exactly-zero z is "no sign" — reversal does NOT fire at the zero bar; it
  fires on the first subsequent bar with a clearly-opposite non-zero sign.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scanner.indicators import mav_diff_z as mod
from scanner.indicators.mav_diff_z import _detect_reversals

FIXTURES_DIR = Path(__file__).parent / "fixtures"
TSC_DIR = FIXTURES_DIR / "tsc_2012"
SYN_DIR = FIXTURES_DIR / "synthetic"

# Test-specific reduced parameters — small windows, short warmup.
MAV1, MAV2, Z_HIST = 3, 5, 4
WARMUP = MAV2 + Z_HIST - 2  # = 7 (first z-score at bar index 7)
PARAMS = dict(mav1=MAV1, mav2=MAV2, z_history=Z_HIST)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_df(closes: list[float]) -> pd.DataFrame:
    n = len(closes)
    c = np.array(closes, dtype=float)
    return pd.DataFrame({
        "date":   pd.date_range("2020-01-01", periods=n, freq="B"),
        "open": c, "high": c * 1.005, "low": c * 0.995,
        "close": c, "volume": [1_000_000.0] * n,
    })


def _load_syn(name: str) -> pd.DataFrame:
    return pd.read_csv(SYN_DIR / f"mav_diff_z_{name}.csv", parse_dates=["date"])


def _load_tsc(ticker: str) -> pd.DataFrame:
    df = pd.read_csv(TSC_DIR / f"{ticker}_ohlcv.csv", parse_dates=["date"])
    return df.iloc[::-1].reset_index(drop=True)


def _ref_z(closes: list[float], mav1: int, mav2: int, z_history: int) -> pd.Series:
    """Independent pandas reference computation of z-score."""
    s = pd.Series(closes, dtype=float)
    diff = s.rolling(mav1).mean() - s.rolling(mav2).mean()
    return (diff - diff.rolling(z_history).mean()) / diff.rolling(z_history).std(ddof=1)


# ---------------------------------------------------------------------------
# 1. Numerical accuracy — formula validated against independent reference
# ---------------------------------------------------------------------------

def test_z_score_matches_reference() -> None:
    """compute_series z_score must match independent pandas computation."""
    closes = [10, 12, 15, 19, 24, 30, 37, 45, 54, 64, 75, 87, 87, 82, 74, 63, 49]
    df = _make_df(closes)
    result = mod.compute_series(df, **PARAMS)
    z_ref = _ref_z(closes, MAV1, MAV2, Z_HIST)

    valid = z_ref.notna() & pd.Series(result["z_score"]).notna()
    assert valid.any(), "No valid z-scores computed — check warmup length"

    diff = (result["z_score"][valid] - z_ref[valid]).abs().max()
    assert diff < 1e-9, f"Max z-score deviation {diff:.2e} exceeds 1e-9"


def test_mav_diff_matches_reference() -> None:
    """mav_diff column must equal SMA(mav1) − SMA(mav2) for all bars."""
    closes = [10, 12, 15, 19, 24, 30, 37, 45, 54, 64, 75, 87]
    df = _make_df(closes)
    result = mod.compute_series(df, **PARAMS)

    s = pd.Series(closes, dtype=float)
    expected_diff = s.rolling(MAV1).mean() - s.rolling(MAV2).mean()

    valid = expected_diff.notna()
    diff = (result["mav_diff"][valid] - expected_diff[valid]).abs().max()
    assert diff < 1e-9, f"mav_diff deviation {diff:.2e}"


def test_z_score_nan_during_warmup() -> None:
    """All z-scores must be NaN during the warmup period.
    Uses pos_to_neg closes (non-constant diffs, so std > 0 once window fills)."""
    # pos_to_neg closes; z-score is valid starting at bar WARMUP=7.
    closes = [10, 12, 15, 19, 24, 30, 37, 45, 54, 64, 75, 87, 87, 82, 74, 63, 49]
    df = _make_df(closes)
    result = mod.compute_series(df, **PARAMS)

    # bars 0 .. WARMUP-1 must all be NaN
    for i in range(WARMUP):
        assert pd.isna(result["z_score"].iloc[i]), (
            f"Bar {i} should be NaN during warmup, got {result['z_score'].iloc[i]}"
        )
    # bar WARMUP must be valid (confirmed: z=+1.1619 for pos_to_neg series)
    assert not pd.isna(result["z_score"].iloc[WARMUP]), (
        f"Bar {WARMUP} should have a valid z-score"
    )


# ---------------------------------------------------------------------------
# 2. Warmup behaviour — no reversal, no error during warmup
# ---------------------------------------------------------------------------

def test_warmup_no_reversal_no_error() -> None:
    """A series shorter than the full warmup must return reversal=False, not raise."""
    closes = list(range(1, WARMUP + 1))  # exactly WARMUP bars — no valid z
    df = _make_df(closes)
    result = mod.compute_series(df, **PARAMS)

    assert result["z_score"].isna().all(), "All z-scores should be NaN"
    assert not result["reversal"].any(), "No reversal should fire in warmup"


def test_first_z_bar_no_reversal() -> None:
    """On the very first bar that has a valid z-score there is no prior z to
    compare against, so reversal must be False.
    Uses exactly WARMUP+1 bars of a non-constant series."""
    # First WARMUP+1 bars of pos_to_neg — gives non-zero std so z is valid at bar WARMUP.
    closes = [10, 12, 15, 19, 24, 30, 37, 45]  # 8 bars; z valid at bar 7 (WARMUP)
    df = _make_df(closes)
    result = mod.compute_series(df, **PARAMS)

    assert not pd.isna(result["z_score"].iloc[WARMUP]), "Expected valid z at bar WARMUP"
    assert not result["reversal"].iloc[WARMUP], (
        "First valid z-score bar must have reversal=False (no prior z to compare)"
    )


# ---------------------------------------------------------------------------
# 3. Sign-change detection — three synthetic CSV fixtures
# ---------------------------------------------------------------------------

def test_pos_to_neg_reversal_fires() -> None:
    """Verified fixture: z crosses positive→negative at bar 13.
    Reversal must be True only at bar 13, False everywhere else."""
    df = _load_syn("pos_to_neg")
    result = mod.compute_series(df, **PARAMS)

    REVERSAL_BAR = 13

    # Must fire at the crossing bar
    assert result["reversal"].iloc[REVERSAL_BAR], (
        f"Reversal expected at bar {REVERSAL_BAR} "
        f"(z={result['z_score'].iloc[REVERSAL_BAR]:.4f})"
    )
    # Must NOT fire before the crossing bar
    assert not result["reversal"].iloc[:REVERSAL_BAR].any(), (
        "No reversal should fire before bar 13"
    )
    # Must NOT fire again immediately after (z stays negative)
    assert not result["reversal"].iloc[REVERSAL_BAR + 1:].any(), (
        "No second reversal should fire while z stays negative"
    )


def test_neg_to_pos_reversal_fires() -> None:
    """Verified fixture: z crosses negative→positive at bar 11."""
    df = _load_syn("neg_to_pos")
    result = mod.compute_series(df, **PARAMS)

    REVERSAL_BAR = 11

    assert result["reversal"].iloc[REVERSAL_BAR], (
        f"Reversal expected at bar {REVERSAL_BAR} "
        f"(z={result['z_score'].iloc[REVERSAL_BAR]:.4f})"
    )
    assert not result["reversal"].iloc[:REVERSAL_BAR].any(), (
        "No reversal should fire before bar 11"
    )
    assert not result["reversal"].iloc[REVERSAL_BAR + 1:].any(), (
        "No second reversal should fire while z stays positive"
    )


def test_holds_sign_no_reversal() -> None:
    """Monotonically rising series — z stays positive, no reversal ever fires."""
    df = _load_syn("holds_sign")
    result = mod.compute_series(df, **PARAMS)

    assert not result["reversal"].any(), (
        f"No reversal expected; firing at: {result.index[result['reversal']].tolist()}"
    )
    valid_z = result["z_score"].dropna()
    assert (valid_z > 0).all(), (
        "All valid z-scores should be positive in a monotonic uptrend"
    )


# ---------------------------------------------------------------------------
# 4. Zero-touch behaviour (inline, no CSV)
# ---------------------------------------------------------------------------

def test_zero_touch_does_not_fire_at_zero_bar() -> None:
    """_detect_reversals: +pos → 0 → -neg — reversal fires at -neg, not at 0."""
    # Construct a z-series directly: warmup NaNs, then positive, then zero, then negative.
    z = pd.Series([np.nan, np.nan, np.nan, 1.16, 0.24, 0.0, -1.46, -1.39])
    result = _detect_reversals(z)

    assert result[0] is False, "NaN bar → no reversal"
    assert result[3] is False, "First valid z (positive) → no reversal (no prior sign)"
    assert result[4] is False, "z stays positive → no reversal"
    assert result[5] is False, "z == 0 → no reversal (zero is 'no sign')"
    assert result[6] is True,  "First non-zero bar with opposite sign → reversal fires"
    assert result[7] is False, "z stays negative → no second reversal"


def test_zero_touch_sign_memory_persists() -> None:
    """_detect_reversals: multiple consecutive zeros don't clear sign memory."""
    z = pd.Series([np.nan, 0.5, 0.0, 0.0, 0.0, -0.3])
    result = _detect_reversals(z)

    assert result[1] is False, "First positive bar → no prior sign"
    assert result[2] is False
    assert result[3] is False
    assert result[4] is False
    assert result[5] is True, "Reversal fires after multiple zeros when sign flips"


def test_zero_touch_no_reversal_on_zero_to_nonzero_no_prior() -> None:
    """No reversal when z goes NaN → 0 → +val (no prior non-zero sign)."""
    z = pd.Series([np.nan, 0.0, 0.5])
    result = _detect_reversals(z)

    assert result[1] is False
    assert result[2] is False, "No prior non-zero sign → no reversal even on first positive bar"


# ---------------------------------------------------------------------------
# 5. Output contract
# ---------------------------------------------------------------------------

def test_output_keys_present() -> None:
    """compute() must return all required keys with correct types."""
    closes = list(range(1, 25))  # enough bars for z-score to appear
    df = _make_df(closes)
    result = mod.compute(df, **PARAMS)

    assert "mav_diff"   in result
    assert "z_score"    in result
    assert "reversal"   in result
    assert "mav1_value" in result
    assert "mav2_value" in result
    assert isinstance(result["reversal"], bool)


def test_compute_returns_none_during_warmup() -> None:
    """z_score and mav_diff must be None (not NaN) in the latest-bar dict during warmup."""
    df = _make_df(list(range(1, WARMUP + 1)))  # exactly WARMUP bars
    result = mod.compute(df, **PARAMS)

    assert result["z_score"] is None, f"Expected None, got {result['z_score']}"
    assert result["reversal"] is False


# ---------------------------------------------------------------------------
# 6. Consistency — compute() == last row of compute_series()
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ticker", ["WTI", "GOLD", "EUR", "JPY", "GBP"])
def test_compute_series_consistency(ticker: str) -> None:
    """Last row of compute_series() must match compute() at default params."""
    df = _load_tsc(ticker)
    latest = mod.compute(df)
    series = mod.compute_series(df)
    last = series.iloc[-1]

    def _close(a: float | None, b: float | None) -> bool:
        if a is None and pd.isna(b):
            return True
        if a is None or b is None:
            return False
        return abs(float(a) - float(b)) < 1e-9

    assert _close(latest["z_score"],    last["z_score"]), "z_score mismatch"
    assert _close(latest["mav_diff"],   last["mav_diff"]), "mav_diff mismatch"
    assert bool(latest["reversal"]) == bool(last["reversal"]), "reversal mismatch"


# ---------------------------------------------------------------------------
# 7. Default-param smoke test on real TSC data
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ticker", ["WTI", "GOLD", "EUR", "JPY", "GBP"])
def test_default_params_no_error(ticker: str) -> None:
    """compute() at default params (mav1=20, mav2=50, z_history=180) must not raise.
    The 2012 fixtures have ~287 bars, which is enough for warmup (50+180-2=228)."""
    df = _load_tsc(ticker)
    result = mod.compute(df)   # default params

    # At 287 bars > 228 warmup: z_score must be valid
    assert result["z_score"] is not None, (
        f"{ticker}: z_score should be non-None with 287 bars (warmup=228)"
    )
    assert isinstance(result["reversal"], bool)
