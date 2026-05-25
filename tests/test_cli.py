"""Tests for src/scanner/cli.py and src/scanner/scoring.py.

Integration tests for the daily-scan orchestrator.  All EODHD HTTP calls are
mocked; storage uses a real DuckDB instance backed by a tmp-path file.

Test groups:
  1. Scoring unit tests — normalize() and score_tickers().
  2. Happy-path run — 3 mock tickers produce stored OHLCV, indicator outputs,
     and combo/ranking results.
  3. Budget exhaustion mid-loop — stops fetching, doesn't crash, produces
     results from stored data.
  4. Failing ticker (404) — logged, skipped, others complete.
  5. No-retry — a failed ticker isn't re-fetched within the same run.
  6. Fetch idempotency — today's bar already stored → fetch not called.
  7. Storage idempotency — running twice doesn't duplicate rows.
  8. Post-ingestion filter — ticker with too few bars excluded before indicators.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from scanner.cli import run_daily
from scanner.data.eodhd import (
    DailyBudgetExceeded,
    EODHDNotFoundError,
)
from scanner.data.storage import Storage
from scanner.scoring import normalize, score_tickers

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def mock_generate_briefing():
    """Ensure no test in this file hits the live Anthropic API."""
    with patch("scanner.cli.generate_briefing", return_value="Mocked briefing") as m:
        yield m

_TODAY = date(2024, 6, 3)  # fixed date so tests don't depend on the real clock


def _make_prices(
    n: int = 300,
    *,
    start: date | None = None,
    close: float = 100.0,
    volume: float = 2_000_000.0,
) -> pd.DataFrame:
    """Synthetic OHLCV DataFrame with *n* business-day bars ending on *start*."""
    end = start or _TODAY
    dates = pd.bdate_range(end=end, periods=n)
    return pd.DataFrame({
        "date":      pd.Series(dates.date),
        "open":      close,
        "high":      close * 1.01,
        "low":       close * 0.99,
        "close":     close,
        "adj_close": close,
        "volume":    float(volume),
    })


def _mock_client_factory(ticker_prices: dict[str, pd.DataFrame]) -> MagicMock:
    """Return a mock EODHDClient whose fetch_eod returns per-ticker data.

    Tickers not in *ticker_prices* raise EODHDNotFoundError.
    """
    client = MagicMock()

    def _fetch(eodhd_ticker: str, **_kwargs) -> pd.DataFrame:
        # eodhd_ticker is e.g. "AAPL.US"; key is "AAPL"
        code = eodhd_ticker.split(".")[0]
        if code not in ticker_prices:
            raise EODHDNotFoundError(f"404 — {eodhd_ticker} not found")
        return ticker_prices[code]

    client.fetch_eod.side_effect = _fetch
    return client


def _db(tmp_path: Path) -> str:
    return str(tmp_path / "test.duckdb")


# ---------------------------------------------------------------------------
# 1. Scoring unit tests
# ---------------------------------------------------------------------------


class TestNormalize:
    def test_rsi_midpoint(self):
        assert normalize("rsi", {"rsi": 50.0}) == pytest.approx(0.5)

    def test_rsi_low(self):
        assert normalize("rsi", {"rsi": 30.0}) == pytest.approx(0.3)

    def test_rsi_high(self):
        assert normalize("rsi", {"rsi": 70.0}) == pytest.approx(0.7)

    def test_mav_breakout_buy(self):
        assert normalize("mav_breakout", {"signal_value": 0.25, "direction": "buy"}) == pytest.approx(0.25)

    def test_mav_breakout_sell(self):
        assert normalize("mav_breakout", {"signal_value": 0.75, "direction": "sell"}) == pytest.approx(0.75)

    def test_box_breakout_neutral(self):
        assert normalize("box_breakout", {"signal_value": 0.5, "direction": "neutral"}) == pytest.approx(0.5)

    def test_stochastic_buy_signal(self):
        # K=15 → signal_value = 0.15
        assert normalize("stochastic", {"signal_value": 0.15, "direction": "buy"}) == pytest.approx(0.15)

    def test_stochastic_neutral(self):
        assert normalize("stochastic", {"signal_value": 0.5, "direction": "neutral"}) == pytest.approx(0.5)

    def test_bollinger_normal_buy_direction(self):
        # signal_value is z-score; direction-based mapping applies
        assert normalize("bollinger_normal", {"signal_value": 2.1, "direction": "buy"}) == pytest.approx(0.25)

    def test_bollinger_normal_sell_direction(self):
        assert normalize("bollinger_normal", {"signal_value": -1.8, "direction": "sell"}) == pytest.approx(0.75)

    def test_bollinger_normal_neutral(self):
        assert normalize("bollinger_normal", {"signal_value": 0.3, "direction": "neutral"}) == pytest.approx(0.5)

    def test_bollinger_contrarian_buy(self):
        assert normalize("bollinger_contrarian", {"signal_value": -1.4, "direction": "buy"}) == pytest.approx(0.25)

    def test_daily_trend_divergence_buy(self):
        assert normalize("daily_trend_divergence", {"signal_value": 0.007, "direction": "buy"}) == pytest.approx(0.25)

    def test_daily_trend_contrarian_sell(self):
        assert normalize("daily_trend_contrarian", {"signal_value": 0.006, "direction": "sell"}) == pytest.approx(0.75)

    def test_volatility_low_percentile(self):
        # Low vol pct = confirm = low score = buy
        assert normalize("volatility", {"percentile": 0.15, "state": "confirm"}) == pytest.approx(0.15)

    def test_volatility_high_percentile(self):
        assert normalize("volatility", {"percentile": 0.85, "state": "reject"}) == pytest.approx(0.85)

    def test_volume_high_percentile(self):
        # High vol pct = confirm = 1 - 0.8 = 0.2 (low = buy)
        assert normalize("volume", {"percentile": 0.8, "state": "confirm"}) == pytest.approx(0.2)

    def test_volume_low_percentile(self):
        # Low vol pct = reject = 1 - 0.1 = 0.9 (high = sell/demote)
        assert normalize("volume", {"percentile": 0.1, "state": "reject"}) == pytest.approx(0.9)

    def test_mav_diff_z_returns_none(self):
        assert normalize("mav_diff_z", {"z_score": 1.5, "reversal": False}) is None

    def test_unknown_indicator_returns_neutral(self):
        assert normalize("unknown_future_indicator", {}) == pytest.approx(0.5)


class TestScoreTickers:
    def _outputs(self, direction: str = "buy") -> dict:
        """All-buy or all-sell indicator outputs for easy combo testing.

        Confirmation indicators use neutral values (0.5 norm each) so they
        don't interfere with the directional assertion — we test direction
        routing here, not confirmation demoting.
        """
        if direction == "buy":
            rsi_val, sv, d = 25.0, 0.25, "buy"
            stoch_sv = 0.15
        else:
            rsi_val, sv, d = 75.0, 0.75, "sell"
            stoch_sv = 0.85

        return {
            "rsi":                    {"rsi": rsi_val, "direction": d, "signal_value": sv},
            "daily_trend_divergence": {"direction": d, "signal_value": sv},
            "daily_trend_contrarian": {"direction": d, "signal_value": sv},
            "bollinger_normal":       {"direction": d, "signal_value": sv, "bollinger_days": 1},
            "bollinger_contrarian":   {"direction": d, "signal_value": sv, "bollinger_days": 1},
            "mav_breakout":           {"direction": d, "signal_value": sv, "breakout_flag": 1,
                                       "days_since_breakout": 3, "narrow_pct": 0.2},
            "box_breakout":           {"direction": d, "signal_value": sv, "days_since_breakout": 2},
            "stochastic":             {"direction": d, "signal_value": stoch_sv},
            # Neutral confirmation (percentile 0.5): norm = 0.5 for both indicators.
            # Keeps confirmation contribution in the middle so it doesn't push the
            # combo out of buy/sell zone in either direction.
            "volatility":             {"percentile": 0.5, "state": "neutral"},
            "volume":                 {"percentile": 0.5, "state": "neutral"},
        }

    def test_all_buy_direction(self):
        outputs = {("AAPL", "US"): self._outputs("buy")}
        df = score_tickers(outputs, _TODAY)
        assert len(df) == 1
        assert df.iloc[0]["direction"] == "buy"
        assert df.iloc[0]["combo_score"] < 0.3

    def test_all_sell_direction(self):
        outputs = {("AAPL", "US"): self._outputs("sell")}
        df = score_tickers(outputs, _TODAY)
        assert df.iloc[0]["direction"] == "sell"
        assert df.iloc[0]["combo_score"] > 0.7

    def test_agreement_count_all_buy(self):
        outputs = {("AAPL", "US"): self._outputs("buy")}
        df = score_tickers(outputs, _TODAY)
        # 8 trade indicators all agree on "buy"
        assert df.iloc[0]["agreement_count"] == 8

    def test_rank_score_nonnegative(self):
        outputs = {("AAPL", "US"): self._outputs("buy")}
        df = score_tickers(outputs, _TODAY)
        assert df.iloc[0]["rank_score"] >= 0.0

    def test_empty_outputs_returns_empty_df(self):
        df = score_tickers({}, _TODAY)
        assert df.empty

    def test_mav_diff_z_not_in_default_combo(self):
        """mav_diff_z must not affect combo_score (it's not in the default combo)."""
        base = self._outputs("buy")
        with_z = dict(base)
        with_z["mav_diff_z"] = {"z_score": 99.0, "reversal": True}
        outputs_base = {("AAPL", "US"): base}
        outputs_with = {("AAPL", "US"): with_z}
        df_base = score_tickers(outputs_base, _TODAY)
        df_with = score_tickers(outputs_with, _TODAY)
        assert df_base.iloc[0]["combo_score"] == pytest.approx(df_with.iloc[0]["combo_score"])

    def test_vol_reject_demotes_rank(self):
        """Volatility 'reject' state must lower rank_score via confirmation_mult."""
        good = self._outputs("buy")
        bad = dict(good)
        bad["volatility"] = {"percentile": 0.85, "state": "reject"}
        df_good = score_tickers({("A", "US"): good}, _TODAY)
        df_bad = score_tickers({("A", "US"): bad}, _TODAY)
        assert df_good.iloc[0]["rank_score"] > df_bad.iloc[0]["rank_score"]

    def test_combination_name_stamped(self):
        outputs = {("AAPL", "US"): self._outputs("buy")}
        df = score_tickers(outputs, _TODAY, combination_name="default")
        assert df.iloc[0]["combination_name"] == "default"

    def test_date_stamped(self):
        outputs = {("AAPL", "US"): self._outputs("buy")}
        df = score_tickers(outputs, _TODAY)
        assert df.iloc[0]["date"] == _TODAY

    def test_sorted_by_rank_score_descending(self):
        # Two tickers: one buy (good confirmation), one neutral
        good = self._outputs("buy")
        neutral = {k: {**v, "direction": "neutral"} for k, v in good.items()}
        neutral["rsi"] = {"rsi": 50.0, "direction": "neutral", "signal_value": 0.5}
        neutral["stochastic"] = {"signal_value": 0.5, "direction": "neutral"}
        outputs = {("AAA", "US"): good, ("BBB", "US"): neutral}
        df = score_tickers(outputs, _TODAY)
        assert df.iloc[0]["rank_score"] >= df.iloc[1]["rank_score"]

    def test_mtf_alignment_matrix(self):
        """Test alignment bonus at 0, 1, 2, 3 aligned resolutions."""
        base = self._outputs("buy")
        
        # 0 aligned (only daily present, and it's opposite or neutral)
        # Wait, if only daily present, it's 1 resolution, bonus is 0 by Rule B.
        # Let's give all 3 resolutions.
        base["box_breakout"] = {"direction": "buy", "signal_value": 0.25}
        base["box_breakout_weekly"] = {"direction": "buy", "signal_value": 0.25}
        base["box_breakout_monthly"] = {"direction": "buy", "signal_value": 0.25}
        
        df_3 = score_tickers({("A", "US"): base}, _TODAY)
        score_3 = df_3.iloc[0]["rank_score"]
        
        # 2 aligned
        base_2 = dict(base)
        base_2["box_breakout_monthly"] = {"direction": "neutral", "signal_value": 0.5}
        df_2 = score_tickers({("A", "US"): base_2}, _TODAY)
        score_2 = df_2.iloc[0]["rank_score"]
        
        # 1 aligned
        base_1 = dict(base_2)
        base_1["box_breakout_weekly"] = {"direction": "sell", "signal_value": 0.75}
        df_1 = score_tickers({("A", "US"): base_1}, _TODAY)
        score_1 = df_1.iloc[0]["rank_score"]
        
        # 0 aligned
        base_0 = dict(base_1)
        base_0["box_breakout"] = {"direction": "sell", "signal_value": 0.75} # Wait, if daily BB is sell, the combo direction might change!
        # Instead, make BB neutral. The combo will still be "buy" because 7 other indicators are buy!
        base_0["box_breakout"] = {"direction": "neutral", "signal_value": 0.5}
        df_0 = score_tickers({("A", "US"): base_0}, _TODAY)
        score_0 = df_0.iloc[0]["rank_score"]
        
        assert score_3 > score_2 > score_1 > score_0

    def test_mtf_rule_a_combo_direction_alignment(self):
        """Rule A: Align to combo direction, not daily BB."""
        base = self._outputs("buy")
        # Make other indicators extremely strong "buy" so combo average stays < 0.3
        base["rsi"]["rsi"] = 0.0
        base["stochastic"]["signal_value"] = 0.0
        base["box_breakout"] = {"direction": "neutral", "signal_value": 0.5}
        # weekly and monthly are buy
        base["box_breakout_weekly"] = {"direction": "buy", "signal_value": 0.25}
        base["box_breakout_monthly"] = {"direction": "buy", "signal_value": 0.25}
        
        df = score_tickers({("A", "US"): base}, _TODAY)
        
        # Combo direction should still be buy
        assert df.iloc[0]["direction"] == "buy"
        
        # Bonus should be applied! n_resolutions=3, aligned=2.
        # Let's compare to when weekly/monthly are neutral.
        base_no_bonus = dict(base)
        base_no_bonus["box_breakout_weekly"] = {"direction": "neutral", "signal_value": 0.5}
        base_no_bonus["box_breakout_monthly"] = {"direction": "neutral", "signal_value": 0.5}
        
        df_no = score_tickers({("A", "US"): base_no_bonus}, _TODAY)
        
        assert df.iloc[0]["rank_score"] > df_no.iloc[0]["rank_score"]

    def test_mtf_combo_daily_only(self):
        """Verify combo_score only relies on daily BB."""
        base = self._outputs("buy")
        df_base = score_tickers({("A", "US"): base}, _TODAY)
        
        base_with_mtf = dict(base)
        base_with_mtf["box_breakout_weekly"] = {"direction": "sell", "signal_value": 0.75}
        df_mtf = score_tickers({("A", "US"): base_with_mtf}, _TODAY)
        
        # combo_score should be exactly equal
        assert df_base.iloc[0]["combo_score"] == pytest.approx(df_mtf.iloc[0]["combo_score"])

    def test_mtf_rule_b_single_resolution_zero_bonus(self):
        """With daily excluded from alignment, having only one MTF resolution
        (e.g. weekly only) gives 0 bonus — the ≥2 gate on [weekly, monthly]
        requires both to be present."""
        base = self._outputs("buy")
        base["box_breakout"] = {"direction": "buy", "signal_value": 0.25}

        # Weekly only — one MTF resolution → 0 bonus
        one_mtf = dict(base)
        one_mtf["box_breakout_weekly"] = {"direction": "buy", "signal_value": 0.25}
        df_one = score_tickers({("A", "US"): one_mtf}, _TODAY)
        score_one = df_one.iloc[0]["rank_score"]

        # No MTF at all → also 0 bonus
        df_none = score_tickers({("A", "US"): base}, _TODAY)
        score_none = df_none.iloc[0]["rank_score"]

        # Both should be identical (0 MTF bonus in each case)
        assert score_one == pytest.approx(score_none)

        # Now add monthly — both MTF resolutions present → bonus fires
        both_mtf = dict(one_mtf)
        both_mtf["box_breakout_monthly"] = {"direction": "buy", "signal_value": 0.25}
        df_both = score_tickers({("A", "US"): both_mtf}, _TODAY)
        score_both = df_both.iloc[0]["rank_score"]

        # 2/2 aligned → full _W_MTF (0.10) bonus
        assert score_both > score_one + 0.09

    def test_alignment_fraction_persisted_matches_rank_contribution(self):
        """alignment_fraction in the output must equal the value fed into
        rank_score via _W_MTF.  No drift between stored and scored."""
        base = self._outputs("buy")
        base["box_breakout"] = {"direction": "buy", "signal_value": 0.25}
        base["box_breakout_weekly"] = {"direction": "buy", "signal_value": 0.25}
        base["box_breakout_monthly"] = {"direction": "neutral", "signal_value": 0.5}

        df = score_tickers({("A", "US"): base}, _TODAY)
        row = df.iloc[0]

        # With daily excluded, weekly=buy (aligned), monthly=neutral (not aligned)
        # n_resolutions=2, resolutions_aligned=1, fraction=0.5
        assert int(row["resolutions_available"]) == 2
        assert int(row["resolutions_aligned"]) == 1
        assert float(row["alignment_fraction"]) == pytest.approx(0.5)

        # Verify the fraction is what actually drove rank_score:
        # rank_score_with_mtf - rank_score_without_mtf ≈ _W_MTF * fraction
        base_no_mtf = {k: v for k, v in base.items()
                       if k not in ("box_breakout_weekly", "box_breakout_monthly")}
        df_no = score_tickers({("A", "US"): base_no_mtf}, _TODAY)
        delta = float(row["rank_score"]) - float(df_no.iloc[0]["rank_score"])
        # _W_MTF=0.10, fraction=0.5 → delta should be 0.05
        assert delta == pytest.approx(0.10 * 0.5, abs=1e-9)

    def test_alignment_fraction_zero_when_gate_fails(self):
        """When the ≥2 gate fails, all three alignment columns should be 0."""
        base = self._outputs("buy")
        # Only daily present (no weekly/monthly)
        df = score_tickers({("A", "US"): base}, _TODAY)
        row = df.iloc[0]
        assert int(row["resolutions_available"]) == 0
        assert int(row["resolutions_aligned"]) == 0
        assert float(row["alignment_fraction"]) == pytest.approx(0.0)

    def test_alignment_fraction_zero_when_neutral(self):
        """When combo direction is neutral, alignment is 0 regardless of MTF data."""
        base = self._outputs("neutral")
        base["box_breakout_weekly"] = {"direction": "buy", "signal_value": 0.25}
        base["box_breakout_monthly"] = {"direction": "buy", "signal_value": 0.25}
        df = score_tickers({("A", "US"): base}, _TODAY)
        row = df.iloc[0]
        assert float(row["alignment_fraction"]) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Fixtures for orchestrator tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def three_tickers() -> dict[str, pd.DataFrame]:
    """Three mock tickers with sufficient price history."""
    return {
        "AAPL": _make_prices(300, close=150.0, volume=5_000_000.0),
        "MSFT": _make_prices(300, close=300.0, volume=4_000_000.0),
        "GOOGL": _make_prices(300, close=130.0, volume=3_000_000.0),
    }


@pytest.fixture()
def three_candidates() -> list[str]:
    return ["AAPL", "MSFT", "GOOGL"]


# ---------------------------------------------------------------------------
# 2. Happy-path run
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_ohlcv_stored(self, tmp_path, three_tickers):
        client = _mock_client_factory(three_tickers)
        db = _db(tmp_path)
        with patch("scanner.data.universe.SAMPLE_UNIVERSE", _sample_universe(["AAPL", "MSFT", "GOOGL"])):
            run_daily(scope="sample", db_path=db, client=client, run_date=_TODAY)
        with Storage(db) as s:
            for ticker in ["AAPL", "MSFT", "GOOGL"]:
                prices = s.read_prices(ticker, "US")
                assert not prices.empty, f"{ticker} prices not stored"
                assert len(prices) == 300

    def test_indicator_outputs_stored(self, tmp_path, three_tickers):
        client = _mock_client_factory(three_tickers)
        db = _db(tmp_path)
        with patch("scanner.data.universe.SAMPLE_UNIVERSE", _sample_universe(["AAPL", "MSFT", "GOOGL"])):
            run_daily(scope="sample", db_path=db, client=client, run_date=_TODAY)
        with Storage(db) as s:
            outputs = s.read_indicator_outputs("AAPL", "US", _TODAY)
            assert len(outputs) > 0, "No indicator outputs stored for AAPL"

    def test_combo_results_stored(self, tmp_path, three_tickers):
        client = _mock_client_factory(three_tickers)
        db = _db(tmp_path)
        with patch("scanner.data.universe.SAMPLE_UNIVERSE", _sample_universe(["AAPL", "MSFT", "GOOGL"])):
            run_daily(scope="sample", db_path=db, client=client, run_date=_TODAY)
        with Storage(db) as s:
            for ticker in ["AAPL", "MSFT", "GOOGL"]:
                combo = s.read_combo_results(ticker, "US", _TODAY)
                assert not combo.empty, f"No combo results for {ticker}"

    def test_summary_counts(self, tmp_path, three_tickers):
        client = _mock_client_factory(three_tickers)
        db = _db(tmp_path)
        with patch("scanner.data.universe.SAMPLE_UNIVERSE", _sample_universe(["AAPL", "MSFT", "GOOGL"])):
            summary = run_daily(scope="sample", db_path=db, client=client, run_date=_TODAY)
        assert summary["fetched"] == 3
        assert summary["survivors"] == 3
        assert summary["ranked"] == 3
        assert summary["status"] == "completed"
        assert not summary["budget_exhausted"]

    def test_all_registered_indicators_ran(self, tmp_path, three_tickers):
        from scanner.indicators import REGISTRY
        client = _mock_client_factory(three_tickers)
        db = _db(tmp_path)
        with patch("scanner.data.universe.SAMPLE_UNIVERSE", _sample_universe(["AAPL", "MSFT", "GOOGL"])):
            run_daily(scope="sample", db_path=db, client=client, run_date=_TODAY)
        with Storage(db) as s:
            outputs = s.read_indicator_outputs("AAPL", "US", _TODAY)
            for ind_name in REGISTRY:
                assert ind_name in outputs, f"Missing indicator output: {ind_name}"

    def test_monthly_alignment_positive_control(self, tmp_path):
        """End-to-end positive control for monthly Box Breakout and alignment.

        Why 5400 bars + May breakout:
          - 5400 bdays ≈ 21.4 years → 249 completed monthly bars after the
            confirmed-close rule drops the partial June period.
          - monthly lookback = 240; the indicator needs n > lookback to iterate.
          - The breakout is placed on the last daily bar of May 2024, which
            rolls up into the last *completed* monthly bar (May 31 close).
          - Daily and weekly resolutions also see the same price spike.
        """
        n = 5400
        prices = _make_prices(n, close=100.0)
        # Place the breakout on the last bar of May 2024 (a completed monthly close)
        may_mask = [d.year == 2024 and d.month == 5 for d in prices["date"]]
        may_indices = [i for i, v in enumerate(may_mask) if v]
        last_may_idx = may_indices[-1]
        prices.iloc[last_may_idx, prices.columns.get_loc("close")] = 110.0
        prices.iloc[last_may_idx, prices.columns.get_loc("high")] = 112.0

        tickers = {"AAPL": prices}
        client = _mock_client_factory(tickers)
        db = _db(tmp_path)
        
        with patch("scanner.data.universe.SAMPLE_UNIVERSE", _sample_universe(["AAPL"])):
            run_daily(scope="sample", db_path=db, client=client, run_date=_TODAY)
            
        with Storage(db) as s:
            # All 3 resolutions must be stored and must have fired "buy".
            # This exercises the full monthly path end-to-end.
            daily_out = s.read_indicator_outputs("AAPL", "US", _TODAY, resolution="daily")
            weekly_out = s.read_indicator_outputs("AAPL", "US", _TODAY, resolution="weekly")
            monthly_out = s.read_indicator_outputs("AAPL", "US", _TODAY, resolution="monthly")

            assert "box_breakout" in daily_out, "daily box_breakout missing"
            assert "box_breakout" in weekly_out, "weekly box_breakout missing"
            assert "box_breakout" in monthly_out, "monthly box_breakout missing"

            assert daily_out["box_breakout"]["direction"] == "buy", "daily BB should be buy"
            assert weekly_out["box_breakout"]["direction"] == "buy", "weekly BB should be buy"
            assert monthly_out["box_breakout"]["direction"] == "buy", "monthly BB should be buy"

            # The combo direction is neutral for this flat-price fixture because the
            # other 9 indicators see no signal on a flat series. That is correct
            # behaviour: combo neutrality gates the alignment bonus (Rule A).
            # What this test validates is that the monthly BB path fires and stores
            # correctly; the rank_score > base comparison is done in the unit tests.
            combo = s.read_combo_results("AAPL", "US", _TODAY)
            assert not combo.empty, "No combo results written"

    def test_monthly_alignment_bonus_at_seam(self, tmp_path):
        """Prove a real orchestrator-computed monthly breakout produces a non-zero
        alignment bonus when the combo direction is non-neutral.

        The flat-base fixture fires monthly but neutralises the combo (the 9 other
        indicators see a flat line). Testing at the orchestrator→scoring seam:
        run the orchestrator to get real per-resolution BB outputs, then inject
        them into a hand-built directional outputs dict and assert rank_score
        is strictly greater with the MTF keys present than without.
        """
        # ── Step 1: run the orchestrator to produce real MTF BB outputs ────
        n = 5400
        prices = _make_prices(n, close=100.0)
        may_mask = [d.year == 2024 and d.month == 5 for d in prices["date"]]
        may_indices = [i for i, v in enumerate(may_mask) if v]
        last_may_idx = may_indices[-1]
        prices.iloc[last_may_idx, prices.columns.get_loc("close")] = 110.0
        prices.iloc[last_may_idx, prices.columns.get_loc("high")] = 112.0

        tickers = {"AAPL": prices}
        client = _mock_client_factory(tickers)
        db = _db(tmp_path)

        with patch("scanner.data.universe.SAMPLE_UNIVERSE", _sample_universe(["AAPL"])):
            run_daily(scope="sample", db_path=db, client=client, run_date=_TODAY)

        # ── Step 2: read back the real per-resolution BB outputs ───────────
        with Storage(db) as s:
            daily_out = s.read_indicator_outputs("AAPL", "US", _TODAY, resolution="daily")
            weekly_out = s.read_indicator_outputs("AAPL", "US", _TODAY, resolution="weekly")
            monthly_out = s.read_indicator_outputs("AAPL", "US", _TODAY, resolution="monthly")

        # Precondition: the orchestrator actually produced all 3 resolutions
        assert "box_breakout" in daily_out, "daily BB missing"
        assert "box_breakout" in weekly_out, "weekly BB missing"
        assert "box_breakout" in monthly_out, "monthly BB missing"

        # ── Step 3: build a directional outputs dict at the seam ───────────
        # Use TestScoreTickers._outputs("buy") as the base (strong buy combo),
        # then overlay the real orchestrator-computed BB outputs.
        base = TestScoreTickers()._outputs("buy")

        # Inject the real orchestrator-produced BB values
        base["box_breakout"] = daily_out["box_breakout"]["raw_value"]
        base["box_breakout_weekly"] = weekly_out["box_breakout"]["raw_value"]
        base["box_breakout_monthly"] = monthly_out["box_breakout"]["raw_value"]

        # ── Step 4: score with MTF keys, then score without ────────────────
        df_with = score_tickers({("AAPL", "US"): base}, _TODAY)
        assert df_with.iloc[0]["direction"] == "buy", "combo must be buy for alignment"

        base_without = {k: v for k, v in base.items()
                        if k not in ("box_breakout_weekly", "box_breakout_monthly")}
        df_without = score_tickers({("AAPL", "US"): base_without}, _TODAY)

        # rank_score with MTF must strictly exceed rank_score without
        assert df_with.iloc[0]["rank_score"] > df_without.iloc[0]["rank_score"], (
            f"MTF bonus was not applied: {df_with.iloc[0]['rank_score']} "
            f"<= {df_without.iloc[0]['rank_score']}"
        )


# ---------------------------------------------------------------------------
# 3. Budget exhaustion mid-loop
# ---------------------------------------------------------------------------


class TestBudgetExhaustion:
    def test_stops_fetching_on_budget_exceeded(self, tmp_path):
        """Budget exhausted after first fetch — second and third tickers not fetched."""
        tickers = {
            "AAPL": _make_prices(300),
            "MSFT": _make_prices(300),
            "GOOGL": _make_prices(300),
        }
        call_count = 0

        def _fetch(eodhd_ticker: str, **_kw) -> pd.DataFrame:
            nonlocal call_count
            code = eodhd_ticker.split(".")[0]
            call_count += 1
            if call_count > 1:
                raise DailyBudgetExceeded("Budget exhausted")
            return tickers[code]

        client = MagicMock()
        client.fetch_eod.side_effect = _fetch

        db = _db(tmp_path)
        with patch("scanner.data.universe.SAMPLE_UNIVERSE", _sample_universe(["AAPL", "MSFT", "GOOGL"])):
            summary = run_daily(
                scope="sample", db_path=db, client=client, run_date=_TODAY,
                daily_budget_limit=1,
            )

        assert summary["budget_exhausted"] is True
        assert summary["status"] == "partial"

    def test_run_does_not_crash_on_budget_exceeded(self, tmp_path):
        """Budget exhausted mid-loop must not raise; run completes normally."""
        call_count = 0

        def _fetch(eodhd_ticker: str, **_kw) -> pd.DataFrame:
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise DailyBudgetExceeded("Budget exhausted")
            return _make_prices(300)

        client = MagicMock()
        client.fetch_eod.side_effect = _fetch

        db = _db(tmp_path)
        with patch("scanner.data.universe.SAMPLE_UNIVERSE", _sample_universe(["AAPL", "MSFT", "GOOGL"])):
            # Must not raise
            summary = run_daily(scope="sample", db_path=db, client=client, run_date=_TODAY)

        assert "status" in summary  # ran to completion

    def test_produces_results_from_stored_data(self, tmp_path):
        """Tickers stored before budget exhaustion must still produce ranked results."""
        # Pre-seed AAPL with enough data so it survives post-ingest filter
        db = _db(tmp_path)
        with Storage(db) as s:
            s.log_run_start(f"{_TODAY}_setup", _TODAY, "sample")
            s.write_prices("AAPL", "US", _make_prices(300))

        # Now run with a client that raises DailyBudgetExceeded immediately
        client = MagicMock()
        client.fetch_eod.side_effect = DailyBudgetExceeded("Budget exhausted")

        with patch("scanner.data.universe.SAMPLE_UNIVERSE", _sample_universe(["AAPL", "MSFT", "GOOGL"])):
            summary = run_daily(scope="sample", db_path=db, client=client, run_date=_TODAY)

        # AAPL was pre-seeded; should still appear in ranked results
        assert summary["survivors"] >= 1
        assert summary["ranked"] >= 1


# ---------------------------------------------------------------------------
# 4. Failing ticker (mock 404)
# ---------------------------------------------------------------------------


class TestFailingTicker:
    def test_404_ticker_skipped_others_complete(self, tmp_path):
        """A 404 response for MSFT must not prevent AAPL and GOOGL from completing."""
        tickers = {
            "AAPL": _make_prices(300),
            "GOOGL": _make_prices(300),
        }
        client = _mock_client_factory(tickers)  # MSFT not in dict → raises 404

        db = _db(tmp_path)
        with patch("scanner.data.universe.SAMPLE_UNIVERSE", _sample_universe(["AAPL", "MSFT", "GOOGL"])):
            summary = run_daily(scope="sample", db_path=db, client=client, run_date=_TODAY)

        assert summary["failed"] == 1
        assert summary["fetched"] == 2
        with Storage(db) as s:
            assert not s.read_prices("AAPL", "US").empty
            assert not s.read_prices("GOOGL", "US").empty
            assert s.read_prices("MSFT", "US").empty

    def test_run_completes_despite_404(self, tmp_path):
        """run_daily must not raise even when a ticker returns 404."""
        client = _mock_client_factory({})  # all tickers → 404
        db = _db(tmp_path)
        with patch("scanner.data.universe.SAMPLE_UNIVERSE", _sample_universe(["AAPL", "MSFT", "GOOGL"])):
            summary = run_daily(scope="sample", db_path=db, client=client, run_date=_TODAY)
        assert summary["fetched"] == 0
        assert summary["failed"] == 3


# ---------------------------------------------------------------------------
# 5. No-retry: a failed ticker is not re-fetched within the same run
# ---------------------------------------------------------------------------


class TestNoRetry:
    def test_failed_ticker_not_retried(self, tmp_path):
        """Each ticker's fetch_eod must be called at most once per run."""
        call_counts: dict[str, int] = {}

        def _fetch(eodhd_ticker: str, **_kw) -> pd.DataFrame:
            code = eodhd_ticker.split(".")[0]
            call_counts[code] = call_counts.get(code, 0) + 1
            if code == "MSFT":
                raise EODHDNotFoundError("404")
            return _make_prices(300)

        client = MagicMock()
        client.fetch_eod.side_effect = _fetch

        db = _db(tmp_path)
        with patch("scanner.data.universe.SAMPLE_UNIVERSE", _sample_universe(["AAPL", "MSFT", "GOOGL"])):
            run_daily(scope="sample", db_path=db, client=client, run_date=_TODAY)

        # Each ticker should have been attempted exactly once
        assert call_counts.get("AAPL", 0) == 1
        assert call_counts.get("MSFT", 0) == 1
        assert call_counts.get("GOOGL", 0) == 1


# ---------------------------------------------------------------------------
# 6. Fetch idempotency
# ---------------------------------------------------------------------------


class TestFetchIdempotency:
    def test_ticker_with_todays_bar_not_refetched(self, tmp_path):
        """A ticker whose today's bar is already in storage must not trigger a fetch."""
        db = _db(tmp_path)
        # Pre-seed AAPL with a bar for _TODAY
        with Storage(db) as s:
            s.log_run_start("setup", _TODAY, "sample")
            prices = _make_prices(300)
            prices.iloc[-1, prices.columns.get_loc("date")] = _TODAY
            s.write_prices("AAPL", "US", prices)

        client = MagicMock()
        client.fetch_eod.return_value = _make_prices(300)

        with patch("scanner.data.universe.SAMPLE_UNIVERSE", _sample_universe(["AAPL"])):
            summary = run_daily(scope="sample", db_path=db, client=client, run_date=_TODAY)

        assert summary["skipped_idempotent"] == 1
        # fetch_eod must not have been called for AAPL
        for call in client.fetch_eod.call_args_list:
            assert "AAPL" not in str(call)

    def test_backfill_flag_forces_refetch(self, tmp_path):
        """A ticker whose today's bar is already in storage MUST be refetched if backfill=True."""
        db = _db(tmp_path)
        with Storage(db) as s:
            s.log_run_start("setup", _TODAY, "sample")
            prices = _make_prices(300)
            prices.iloc[-1, prices.columns.get_loc("date")] = _TODAY
            s.write_prices("AAPL", "US", prices)

        client = MagicMock()
        client.fetch_eod.return_value = _make_prices(300)

        with patch("scanner.data.universe.SAMPLE_UNIVERSE", _sample_universe(["AAPL"])):
            summary = run_daily(
                scope="sample",
                db_path=db,
                client=client,
                run_date=_TODAY,
                backfill=True,
            )

        assert summary["fetched"] == 1
        assert summary["skipped_idempotent"] == 0
        client.fetch_eod.assert_called_once_with("AAPL.US")

    def test_ticker_with_older_bar_is_fetched(self, tmp_path):
        """A ticker whose latest bar is yesterday must be fetched."""
        db = _db(tmp_path)
        yesterday = _TODAY - timedelta(days=1)
        with Storage(db) as s:
            s.log_run_start("setup", _TODAY, "sample")
            prices = _make_prices(300, start=yesterday)
            s.write_prices("AAPL", "US", prices)

        client = MagicMock()
        client.fetch_eod.return_value = _make_prices(300)

        with patch("scanner.data.universe.SAMPLE_UNIVERSE", _sample_universe(["AAPL"])):
            run_daily(scope="sample", db_path=db, client=client, run_date=_TODAY)

        # Fetch must have been called
        assert client.fetch_eod.call_count >= 1


# ---------------------------------------------------------------------------
# 7. Storage idempotency
# ---------------------------------------------------------------------------


class TestStorageIdempotency:
    def test_running_twice_no_duplicate_rows(self, tmp_path):
        """Running run_daily twice on the same day must not duplicate storage rows."""
        db = _db(tmp_path)
        client = _mock_client_factory({"AAPL": _make_prices(300)})

        with patch("scanner.data.universe.SAMPLE_UNIVERSE", _sample_universe(["AAPL"])):
            run_daily(scope="sample", db_path=db, client=client, run_date=_TODAY)

        # Re-run: AAPL bar is now stored → idempotency check skips fetch
        with patch("scanner.data.universe.SAMPLE_UNIVERSE", _sample_universe(["AAPL"])):
            run_daily(scope="sample", db_path=db, client=client, run_date=_TODAY)

        with Storage(db) as s:
            prices = s.read_prices("AAPL", "US")
            # No duplicated date rows (upsert semantics)
            assert prices["date"].duplicated().sum() == 0

            # Indicator output for today must appear exactly once per indicator
            outputs_raw = s._con.execute(
                "SELECT COUNT(*) FROM tbl_indicator_outputs "
                "WHERE ticker='AAPL' AND exchange='US' AND date=?",
                [_TODAY],
            ).fetchone()[0]
            # Should be exactly the number of indicators plus 2 for MTF Box Breakout
            from scanner.indicators import REGISTRY
            assert outputs_raw == len(REGISTRY) + 2

            # Combo results: one row per combination per day
            combo_raw = s._con.execute(
                "SELECT COUNT(*) FROM tbl_combo_results "
                "WHERE ticker='AAPL' AND exchange='US' AND date=?",
                [_TODAY],
            ).fetchone()[0]
            assert combo_raw == 1  # one combination ("default")


# ---------------------------------------------------------------------------
# 8. Post-ingestion filter
# ---------------------------------------------------------------------------


class TestPostIngestionFilter:
    def test_ticker_with_few_bars_excluded_from_indicators(self, tmp_path):
        """A ticker with fewer than min_history_bars (default 250) must be excluded
        from the indicator run even if it was fetched successfully."""
        # MSFT gets only 10 bars → excluded; AAPL gets 300 → survives
        tickers = {
            "AAPL": _make_prices(300),
            "MSFT": _make_prices(10),  # too few bars
        }
        client = _mock_client_factory(tickers)
        db = _db(tmp_path)

        with patch("scanner.data.universe.SAMPLE_UNIVERSE", _sample_universe(["AAPL", "MSFT"])):
            summary = run_daily(scope="sample", db_path=db, client=client, run_date=_TODAY)

        # AAPL survives; MSFT filtered out
        assert summary["survivors"] == 1
        assert summary["ranked"] == 1

        with Storage(db) as s:
            # AAPL has indicator outputs
            aapl_outputs = s.read_indicator_outputs("AAPL", "US", _TODAY)
            assert len(aapl_outputs) > 0

            # MSFT has no indicator outputs (filtered before indicator run)
            msft_outputs = s.read_indicator_outputs("MSFT", "US", _TODAY)
            assert len(msft_outputs) == 0


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------


def _sample_universe(tickers: list[str]) -> list[dict]:
    """Minimal SAMPLE_UNIVERSE entries for the given ticker list."""
    return [
        {
            "ticker":          t,
            "exchange":        "US",
            "name":            f"{t} Corp",
            "currency":        "USD",
            "market_cap_usd":  1.0e12,
            "sector":          "Technology",
            "region":          "North America",
        }
        for t in tickers
    ]

# ---------------------------------------------------------------------------
# 9. Report Pipeline (D5)
# ---------------------------------------------------------------------------

class TestReportPipeline:
    def test_pipeline_success(self, mock_generate_briefing, tmp_path):
        """Happy path: JSON and Excel are written, briefing is generated, email is sent."""
        mock_generate_briefing.return_value = "Mocked AI briefing text."
        
        tickers = {"AAPL": _make_prices(300)}
        client = _mock_client_factory(tickers)
        db = _db(tmp_path)
        report_dir = tmp_path / "reports"
        
        class FakeTransport:
            called = False
            def send(self, **kwargs):
                self.called = True
                self.kwargs = kwargs
                from scanner.report.email import SendResult
                return SendResult(id="fake-123")
                
        transport = FakeTransport()
        
        with patch("scanner.data.universe.SAMPLE_UNIVERSE", _sample_universe(["AAPL"])):
            summary = run_daily(
                scope="sample",
                db_path=db,
                client=client,
                run_date=_TODAY,
                report_dir=report_dir,
                send_email=True,
                recipients=["test@example.com"],
                from_addr="from@example.com",
                email_transport=transport,
            )
            
        assert summary["briefing_generated"] is True
        assert summary["email_sent"] is True
        
        # Verify JSON and Excel files exist
        run_id = f"{_TODAY}_sample"
        assert (report_dir / f"{run_id}_dashboard.json").exists()
        assert (report_dir / f"{run_id}_report.xlsx").exists()
        
        # Verify transport was called with correct data
        assert transport.called is True
        assert "Mocked AI briefing text." in transport.kwargs["html"]
        assert transport.kwargs["attachments"][0]["filename"] == f"{run_id}_report.xlsx"
        assert transport.kwargs["to"] == ["test@example.com"]

    def test_briefing_fail_soft(self, mock_generate_briefing, tmp_path):
        """If generate_briefing returns None, the run continues, records None, and email sends."""
        mock_generate_briefing.return_value = None
        
        tickers = {"AAPL": _make_prices(300)}
        client = _mock_client_factory(tickers)
        db = _db(tmp_path)
        report_dir = tmp_path / "reports"
        
        class FakeTransport:
            def send(self, **kwargs):
                from scanner.report.email import SendResult
                return SendResult(id="fake-123")
                
        transport = FakeTransport()
        
        with patch("scanner.data.universe.SAMPLE_UNIVERSE", _sample_universe(["AAPL"])):
            summary = run_daily(
                scope="sample",
                db_path=db,
                client=client,
                run_date=_TODAY,
                report_dir=report_dir,
                send_email=True,
                recipients=["test@example.com"],
                from_addr="from@example.com",
                email_transport=transport,
            )
            
        assert summary["briefing_generated"] is False
        assert summary["email_sent"] is True
        
        # Verify DB logged it correctly
        with Storage(db) as s:
            run_id = f"{_TODAY}_sample"
            row = s._con.execute(
                "SELECT briefing_generated, email_sent FROM tbl_run_log WHERE run_id = ?",
                [run_id]
            ).fetchone()
            assert row[0] is False  # briefing_generated
            assert row[1] is True   # email_sent

    def test_email_send_error_caught(self, mock_generate_briefing, tmp_path):
        """If send_report raises SendError, run_daily catches it and marks email_sent=False."""
        mock_generate_briefing.return_value = "Briefing."
        
        tickers = {"AAPL": _make_prices(300)}
        client = _mock_client_factory(tickers)
        db = _db(tmp_path)
        report_dir = tmp_path / "reports"
        
        class FakeFailingTransport:
            def send(self, **kwargs):
                from scanner.report.email import SendError
                raise SendError("Simulated Resend API failure.")
                
        transport = FakeFailingTransport()
        
        with patch("scanner.data.universe.SAMPLE_UNIVERSE", _sample_universe(["AAPL"])):
            summary = run_daily(
                scope="sample",
                db_path=db,
                client=client,
                run_date=_TODAY,
                report_dir=report_dir,
                send_email=True,
                recipients=["test@example.com"],
                from_addr="from@example.com",
                email_transport=transport,
            )
            
        assert summary["briefing_generated"] is True
        assert summary["email_sent"] is False
        assert summary["status"] == "completed"  # email failure shouldn't crash or alter fetch status
