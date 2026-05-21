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
            # Should be exactly the number of indicators (no duplicates)
            from scanner.indicators import REGISTRY
            assert outputs_raw == len(REGISTRY)

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
