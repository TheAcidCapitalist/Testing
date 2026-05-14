"""Unit tests for the indicator registry auto-discovery."""

from __future__ import annotations

import pytest

from scanner.indicators import REGISTRY, BaseIndicator


EXPECTED_INDICATORS = {
    "rsi",
    "stochastic",
    "mav_breakout",
    "daily_trend",
    "bollinger",
    "volatility",
    "volume",
    "mav_diff_z",
}


def test_registry_discovers_all_indicators() -> None:
    assert EXPECTED_INDICATORS == set(REGISTRY.keys()), (
        f"Missing: {EXPECTED_INDICATORS - set(REGISTRY.keys())}, "
        f"Extra: {set(REGISTRY.keys()) - EXPECTED_INDICATORS}"
    )


def test_all_registry_entries_are_base_indicator_subclasses() -> None:
    for name, cls in REGISTRY.items():
        assert issubclass(cls, BaseIndicator), f"{name!r} is not a BaseIndicator subclass"
        assert hasattr(cls, "name"), f"{name!r} missing class-level 'name' attribute"


def test_each_indicator_computes_score_and_signal_columns(sample_ohlcv) -> None:
    for ind_name, cls in REGISTRY.items():
        result = cls().compute(sample_ohlcv.copy())
        score_col = f"{ind_name}_score"
        signal_col = f"{ind_name}_signal"
        assert score_col in result.columns, f"{ind_name}: missing {score_col!r}"
        assert signal_col in result.columns, f"{ind_name}: missing {signal_col!r}"
        # Scores must be in [0, 1] (ignoring NaN from warm-up period)
        valid = result[score_col].dropna()
        assert (valid >= 0).all() and (valid <= 1).all(), (
            f"{ind_name}: score out of [0, 1] range"
        )
