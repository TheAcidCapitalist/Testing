"""Tests for src/scanner/data/universe.py.

Storage interactions use a real in-memory DuckDB instance (consistent with the
eodhd.py test approach) so filter behaviour is exercised through actual SQL.
"""

from __future__ import annotations

import pandas as pd
import pytest

from scanner.data.storage import Storage
from scanner.data.universe import (
    CANDIDATE_COLUMNS,
    SAMPLE_UNIVERSE,
    ProductionScopeUnavailable,
    apply_post_ingest_filters,
    candidates,
    compute_adv,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _prices(n: int, *, close: float = 10.0, volume: float = 1_000_000) -> pd.DataFrame:
    """Synthetic price DataFrame with *n* business-day bars."""
    dates = pd.date_range("2020-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "date":      dates,
        "open":      close,
        "high":      close * 1.01,
        "low":       close * 0.99,
        "close":     close,
        "adj_close": close,
        "volume":    float(volume),
    })


def _one_candidate(
    ticker: str = "TEST",
    exchange: str = "US",
    market_cap_usd: float = 1e12,
) -> pd.DataFrame:
    """One-row candidates DataFrame."""
    return pd.DataFrame([{
        "ticker":        ticker,
        "exchange":      exchange,
        "name":          "Test Corp",
        "currency":      "USD",
        "market_cap_usd": market_cap_usd,
        "sector":        "Technology",
        "region":        "North America",
    }])


@pytest.fixture()
def storage():
    with Storage(":memory:") as s:
        yield s


# ── candidates — sample scope ─────────────────────────────────────────────────


class TestCandidatesSampleScope:
    def test_returns_dataframe(self):
        assert isinstance(candidates("sample"), pd.DataFrame)

    def test_has_expected_columns(self):
        assert list(candidates("sample").columns) == CANDIDATE_COLUMNS

    def test_has_15_entries_before_cap_filter(self):
        df = candidates("sample", min_market_cap_usd=0)
        assert len(df) == 15

    def test_all_15_pass_default_market_cap(self):
        """Every sample ticker must clear the settled $750M floor."""
        df = candidates("sample", min_market_cap_usd=750_000_000)
        assert len(df) == 15

    def test_high_threshold_reduces_count(self):
        """A threshold above some sample caps must shrink the result."""
        df = candidates("sample", min_market_cap_usd=5e12)
        assert len(df) < 15

    def test_market_cap_threshold_is_inclusive(self):
        """A row at exactly the threshold must be included (>= not >)."""
        min_cap = min(r["market_cap_usd"] for r in SAMPLE_UNIVERSE)
        at_threshold = candidates("sample", min_market_cap_usd=min_cap)
        above_threshold = candidates("sample", min_market_cap_usd=min_cap + 1)
        assert len(at_threshold) > len(above_threshold)

    def test_zero_threshold_returns_all_rows(self):
        df = candidates("sample", min_market_cap_usd=0)
        assert len(df) == len(SAMPLE_UNIVERSE)

    def test_index_reset_after_filter(self):
        """Index must be 0..N-1 after a filtering pass, not a stale slice."""
        df = candidates("sample", min_market_cap_usd=5e12)
        if len(df) > 0:
            assert list(df.index) == list(range(len(df)))

    def test_string_fields_non_empty(self):
        df = candidates("sample")
        for col in ("ticker", "exchange", "name", "currency", "sector", "region"):
            assert (df[col].str.len() > 0).all(), f"column '{col}' has empty strings"

    def test_market_cap_values_positive(self):
        assert (candidates("sample")["market_cap_usd"] > 0).all()

    def test_all_exchanges_are_us(self):
        """Test-phase universe is US-only (non-US coverage deferred)."""
        df = candidates("sample")
        assert (df["exchange"] == "US").all()

    def test_sectors_cover_multiple_industries(self):
        """Sample list should span at least 5 distinct sectors."""
        df = candidates("sample")
        assert df["sector"].nunique() >= 5


# ── candidates — gated scopes ─────────────────────────────────────────────────


class TestCandidatesGatedScopes:
    def test_us_raises_paid_tier_required(self):
        with pytest.raises(ProductionScopeUnavailable):
            candidates("us")

    def test_global_raises_paid_tier_required(self):
        with pytest.raises(ProductionScopeUnavailable):
            candidates("global")

    def test_paid_tier_message_references_spec(self):
        with pytest.raises(ProductionScopeUnavailable, match="phase-c-plan"):
            candidates("us")

    def test_paid_tier_required_is_exception_subclass(self):
        assert issubclass(ProductionScopeUnavailable, Exception)

    def test_unknown_scope_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown scope"):
            candidates("narrow")  # type: ignore[arg-type]


# ── compute_adv ───────────────────────────────────────────────────────────────


class TestComputeAdv:
    def test_correct_value(self, storage):
        # close=10, volume=500_000 → dollar-volume per bar = 5_000_000
        storage.write_prices("AAPL", "US", _prices(20, close=10.0, volume=500_000))
        assert compute_adv(storage, "AAPL", "US") == pytest.approx(5_000_000)

    def test_empty_storage_returns_none(self, storage):
        assert compute_adv(storage, "AAPL", "US") is None

    def test_configurable_window(self, storage):
        """ADV with window=5 must only use the last 5 bars."""
        dates = pd.date_range("2020-01-01", periods=20, freq="B")
        df = pd.DataFrame({
            "date":      dates,
            "open":      10.0,
            "high":      10.1,
            "low":        9.9,
            "close":     10.0,
            "adj_close": 10.0,
            # first 15 bars: high volume; last 5: low volume
            "volume": [2_000_000] * 15 + [100_000] * 5,
        })
        storage.write_prices("AAPL", "US", df)
        adv_5  = compute_adv(storage, "AAPL", "US", window=5)
        adv_20 = compute_adv(storage, "AAPL", "US", window=20)
        # window=5 sees only the low-volume tail → lower ADV
        assert adv_5 < adv_20

    def test_fewer_bars_than_window_uses_all(self, storage):
        """No error when stored bars < window; all available bars are used."""
        storage.write_prices("AAPL", "US", _prices(5, close=10.0, volume=500_000))
        assert compute_adv(storage, "AAPL", "US", window=20) == pytest.approx(5_000_000)

    def test_window_one_uses_single_last_bar(self, storage):
        """window=1 must return the dollar-volume of exactly the last bar."""
        dates = pd.date_range("2020-01-01", periods=10, freq="B")
        df = pd.DataFrame({
            "date":      dates,
            "open":      10.0, "high": 10.1, "low": 9.9,
            "close":     10.0, "adj_close": 10.0,
            "volume":    [100_000] * 9 + [999_000],  # last bar has distinctive volume
        })
        storage.write_prices("AAPL", "US", df)
        assert compute_adv(storage, "AAPL", "US", window=1) == pytest.approx(10.0 * 999_000)


# ── apply_post_ingest_filters ─────────────────────────────────────────────────


class TestApplyPostIngestFilters:
    def test_passes_all_filters(self, storage):
        """Healthy ticker (300 bars, good price, high ADV) passes."""
        storage.write_prices("TEST", "US", _prices(300, close=10.0, volume=1_000_000))
        result = apply_post_ingest_filters(
            _one_candidate(), storage,
            min_avg_daily_value=5_000_000, min_price=1.0, min_history_bars=250,
        )
        assert len(result) == 1

    def test_excludes_no_stored_prices(self, storage):
        """Candidate with no prices in storage must be excluded."""
        result = apply_post_ingest_filters(
            _one_candidate(), storage,
            min_avg_daily_value=5_000_000, min_price=1.0, min_history_bars=250,
        )
        assert len(result) == 0

    def test_excludes_below_min_history_bars(self, storage):
        """249 bars when 250 required → excluded."""
        storage.write_prices("TEST", "US", _prices(249, close=10.0, volume=1_000_000))
        result = apply_post_ingest_filters(
            _one_candidate(), storage,
            min_avg_daily_value=5_000_000, min_price=1.0, min_history_bars=250,
        )
        assert len(result) == 0

    def test_includes_at_min_history_bars(self, storage):
        """Exactly 250 bars when 250 required → passes (>= not >)."""
        storage.write_prices("TEST", "US", _prices(250, close=10.0, volume=1_000_000))
        result = apply_post_ingest_filters(
            _one_candidate(), storage,
            min_avg_daily_value=5_000_000, min_price=1.0, min_history_bars=250,
        )
        assert len(result) == 1

    def test_excludes_below_min_price(self, storage):
        """Latest close below min_price → excluded."""
        storage.write_prices("TEST", "US", _prices(300, close=0.50, volume=1_000_000))
        result = apply_post_ingest_filters(
            _one_candidate(), storage,
            min_avg_daily_value=1.0, min_price=1.0, min_history_bars=10,
        )
        assert len(result) == 0

    def test_includes_at_min_price(self, storage):
        """Latest close exactly at min_price passes (>=)."""
        storage.write_prices("TEST", "US", _prices(300, close=1.0, volume=10_000_000))
        result = apply_post_ingest_filters(
            _one_candidate(), storage,
            min_avg_daily_value=1.0, min_price=1.0, min_history_bars=10,
        )
        assert len(result) == 1

    def test_excludes_below_min_adv(self, storage):
        """Dollar-volume well below threshold → excluded."""
        # close=1, volume=1 → ADV=$1
        storage.write_prices("TEST", "US", _prices(300, close=1.0, volume=1))
        result = apply_post_ingest_filters(
            _one_candidate(), storage,
            min_avg_daily_value=5_000_000, min_price=1.0, min_history_bars=250,
        )
        assert len(result) == 0

    def test_includes_at_min_adv(self, storage):
        """ADV exactly at threshold passes (>=)."""
        # close=10, volume=500_000 → ADV=$5_000_000 exactly
        storage.write_prices("TEST", "US", _prices(300, close=10.0, volume=500_000))
        result = apply_post_ingest_filters(
            _one_candidate(), storage,
            min_avg_daily_value=5_000_000, min_price=1.0, min_history_bars=250,
        )
        assert len(result) == 1

    def test_only_passing_candidate_returned(self, storage):
        """One pass, one fail → only the passing ticker in the result."""
        storage.write_prices("AAPL", "US", _prices(300, close=10.0, volume=1_000_000))
        # ZZZZ has no prices → excluded
        c = pd.DataFrame([
            {"ticker": "AAPL", "exchange": "US", "name": "Apple", "currency": "USD",
             "market_cap_usd": 1e12, "sector": "Technology", "region": "North America"},
            {"ticker": "ZZZZ", "exchange": "US", "name": "Dummy", "currency": "USD",
             "market_cap_usd": 1e12, "sector": "Technology", "region": "North America"},
        ])
        result = apply_post_ingest_filters(
            c, storage,
            min_avg_daily_value=5_000_000, min_price=1.0, min_history_bars=250,
        )
        assert len(result) == 1
        assert result.iloc[0]["ticker"] == "AAPL"

    def test_empty_result_has_correct_columns(self, storage):
        """No-pass case returns empty DataFrame preserving input columns."""
        c = _one_candidate()
        result = apply_post_ingest_filters(
            c, storage,
            min_avg_daily_value=5_000_000, min_price=1.0, min_history_bars=250,
        )
        assert list(result.columns) == list(c.columns)
        assert len(result) == 0

    def test_result_index_is_reset(self, storage):
        """Result index must be 0..N-1."""
        c = pd.DataFrame([
            {"ticker": "A", "exchange": "US", "name": "A Corp", "currency": "USD",
             "market_cap_usd": 1e12, "sector": "Tech", "region": "NA"},
            {"ticker": "B", "exchange": "US", "name": "B Corp", "currency": "USD",
             "market_cap_usd": 1e12, "sector": "Tech", "region": "NA"},
        ])
        storage.write_prices("A", "US", _prices(300, close=10.0, volume=1_000_000))
        storage.write_prices("B", "US", _prices(300, close=10.0, volume=1_000_000))
        result = apply_post_ingest_filters(
            c, storage,
            min_avg_daily_value=5_000_000, min_price=1.0, min_history_bars=250,
        )
        assert list(result.index) == list(range(len(result)))

    def test_adv_window_parameter_respected(self, storage):
        """adv_window controls how many recent bars feed the ADV computation."""
        # 300 bars total.  Last 5 bars have near-zero volume; earlier bars have high volume.
        dates = pd.date_range("2020-01-01", periods=300, freq="B")
        df = pd.DataFrame({
            "date":      dates,
            "open":      10.0, "high": 10.1, "low": 9.9,
            "close":     10.0, "adj_close": 10.0,
            "volume":    [5_000_000] * 295 + [1] * 5,
        })
        storage.write_prices("TEST", "US", df)
        c = _one_candidate()

        # window=5: last 5 bars → ADV ≈ $10 → fails $5M
        result_short = apply_post_ingest_filters(
            c, storage,
            min_avg_daily_value=5_000_000, min_price=1.0,
            min_history_bars=250, adv_window=5,
        )
        # window=20: mixes 15 high-volume bars + 5 near-zero → mean still >> $5M
        result_long = apply_post_ingest_filters(
            c, storage,
            min_avg_daily_value=5_000_000, min_price=1.0,
            min_history_bars=250, adv_window=20,
        )
        assert len(result_short) == 0
        assert len(result_long) == 1

    def test_all_sample_candidates_pass_with_adequate_prices(self, storage):
        """Every candidate in the sample universe passes once prices are present."""
        cands = candidates("sample")
        for _, row in cands.iterrows():
            storage.write_prices(
                row["ticker"], row["exchange"],
                _prices(300, close=50.0, volume=5_000_000),
            )
        result = apply_post_ingest_filters(
            cands, storage,
            min_avg_daily_value=5_000_000, min_price=1.0, min_history_bars=250,
        )
        assert len(result) == len(cands)
