"""Unit tests for the scoring engine."""

from __future__ import annotations

import pandas as pd
import pytest

from scanner.indicators import REGISTRY
from scanner.scoring import WEIGHTS, compute_combo_score, rank_results


def _make_scored_df(sample_ohlcv: pd.DataFrame) -> pd.DataFrame:
    """Run all indicators on a sample OHLCV DataFrame and return the last row."""
    df = sample_ohlcv.copy()
    for cls in REGISTRY.values():
        df = cls().compute(df)
    last = df.iloc[[-1]].copy()
    last["ticker"] = "TEST"
    last["exchange"] = "US"
    return last


def test_weights_sum_to_one() -> None:
    total = sum(WEIGHTS.values())
    assert abs(total - 1.0) < 1e-9, f"Weights sum to {total}, expected 1.0"


def test_combo_score_in_unit_interval(sample_ohlcv) -> None:
    df = _make_scored_df(sample_ohlcv)
    result = compute_combo_score(df)
    score = result["combo_score"].iloc[0]
    assert 0.0 <= score <= 1.0, f"combo_score={score} out of [0, 1]"


def test_tier_assigned(sample_ohlcv) -> None:
    df = _make_scored_df(sample_ohlcv)
    result = compute_combo_score(df)
    assert result["tier"].iloc[0] in {"strong", "moderate", "weak"}


def test_rank_results_excludes_falling_knives(sample_ohlcv) -> None:
    """Tickers with daily_trend==down and combo_score<0.4 must be excluded."""
    df = _make_scored_df(sample_ohlcv)
    df = compute_combo_score(df)
    df["daily_trend_signal"] = "down"
    df["combo_score"] = 0.3
    ranked = rank_results(df.copy())
    assert ranked.empty, "Falling knife should have been excluded"


def test_rank_results_sorted_descending(sample_ohlcv) -> None:
    row = _make_scored_df(sample_ohlcv)
    row = compute_combo_score(row)
    rows = pd.concat([row.assign(combo_score=0.9), row.assign(combo_score=0.5)], ignore_index=True)
    ranked = rank_results(rows)
    scores = ranked["combo_score"].tolist()
    assert scores == sorted(scores, reverse=True)
