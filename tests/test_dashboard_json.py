"""Tests for src/scanner/report/dashboard_json.py."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

from scanner.data.storage import Storage
from scanner.report.dashboard_json import build_dashboard_dict, write_dashboard_json

# ── Helpers ──────────────────────────────────────────────────────────────────

_RUN_DATE = date(2024, 6, 15)


def _seed_storage(db: Storage) -> None:
    """Populate storage with 3 tickers, combo results, indicator outputs, and universe."""
    # Universe metadata
    db.write_universe(pd.DataFrame([
        {
            "ticker": "AAPL", "exchange": "US", "name": "Apple Inc.",
            "currency": "USD", "market_cap_usd": 3e12,
            "sector": "Technology", "region": "North America",
        },
        {
            "ticker": "MSFT", "exchange": "US", "name": "Microsoft Corp.",
            "currency": "USD", "market_cap_usd": 2.8e12,
            "sector": "Technology", "region": "North America",
        },
        {
            "ticker": "XOM", "exchange": "US", "name": "Exxon Mobil",
            "currency": "USD", "market_cap_usd": 4e11,
            "sector": "Energy", "region": "North America",
        },
    ]))

    # Combo results — deliberate unsorted order (MSFT highest rank, XOM lowest)
    combo_df = pd.DataFrame([
        {
            "ticker": "XOM", "exchange": "US", "date": _RUN_DATE,
            "combination_name": "default", "direction": "neutral",
            "combo_score": 0.50, "rank_score": 0.30,
            "agreement_count": 0, "n_trade_indicators": 8,
            "signals_firing": json.dumps([]),
            "vol_confirmation": "neutral", "volume_confirmation": "neutral",
            "days_since_breakout": None,
            "resolutions_available": 0, "resolutions_aligned": 0,
            "alignment_fraction": 0.0,
        },
        {
            "ticker": "MSFT", "exchange": "US", "date": _RUN_DATE,
            "combination_name": "default", "direction": "buy",
            "combo_score": 0.28, "rank_score": 0.82,
            "agreement_count": 6, "n_trade_indicators": 8,
            "signals_firing": json.dumps(["rsi", "stochastic", "box_breakout",
                                          "mav_breakout", "bollinger_normal",
                                          "daily_trend_divergence"]),
            "vol_confirmation": "confirm", "volume_confirmation": "confirm",
            "days_since_breakout": 2,
            "resolutions_available": 2, "resolutions_aligned": 2,
            "alignment_fraction": 1.0,
        },
        {
            "ticker": "AAPL", "exchange": "US", "date": _RUN_DATE,
            "combination_name": "default", "direction": "sell",
            "combo_score": 0.75, "rank_score": 0.65,
            "agreement_count": 5, "n_trade_indicators": 8,
            "signals_firing": json.dumps(["rsi", "bollinger_normal",
                                          "stochastic", "daily_trend_divergence",
                                          "daily_trend_contrarian"]),
            "vol_confirmation": "confirm", "volume_confirmation": "reject",
            "days_since_breakout": 7,
            "resolutions_available": 2, "resolutions_aligned": 1,
            "alignment_fraction": 0.5,
        },
    ])
    db.write_combo_results(combo_df)

    # Indicator outputs — MSFT (buy): full set, AAPL (sell): partial, XOM (neutral): minimal
    msft_indicators = [
        {"ticker": "MSFT", "exchange": "US", "date": _RUN_DATE,
         "indicator_name": "rsi",
         "raw_value": {"signal_value": 0.30, "rsi": 30.0},
         "normalized_value": 0.30, "direction": "buy"},
        {"ticker": "MSFT", "exchange": "US", "date": _RUN_DATE,
         "indicator_name": "box_breakout",
         "raw_value": {"signal_value": 0.25, "direction": "buy",
                       "days_since_breakout": 2, "box_high": 420.0,
                       "box_low": 400.0},
         "normalized_value": 0.25, "direction": "buy"},
        {"ticker": "MSFT", "exchange": "US", "date": _RUN_DATE,
         "indicator_name": "stochastic",
         "raw_value": {"signal_value": 0.15, "stoch_k": 15.0, "stoch_d": 18.0},
         "normalized_value": 0.15, "direction": "buy"},
    ]
    # Weekly/monthly box breakout for MSFT
    msft_indicators.append({
        "ticker": "MSFT", "exchange": "US", "date": _RUN_DATE,
        "indicator_name": "box_breakout", "resolution": "weekly",
        "raw_value": {"signal_value": 0.25, "direction": "buy",
                      "days_since_breakout": 1},
        "normalized_value": 0.25, "direction": "buy",
    })
    msft_indicators.append({
        "ticker": "MSFT", "exchange": "US", "date": _RUN_DATE,
        "indicator_name": "box_breakout", "resolution": "monthly",
        "raw_value": {"signal_value": 0.25, "direction": "buy",
                      "days_since_breakout": 0},
        "normalized_value": 0.25, "direction": "buy",
    })
    db.write_indicator_outputs(msft_indicators)

    # AAPL — sell, one indicator
    db.write_indicator_outputs([
        {"ticker": "AAPL", "exchange": "US", "date": _RUN_DATE,
         "indicator_name": "rsi",
         "raw_value": {"signal_value": 0.80, "rsi": 80.0},
         "normalized_value": 0.80, "direction": "sell"},
    ])

    # XOM — neutral, one indicator
    db.write_indicator_outputs([
        {"ticker": "XOM", "exchange": "US", "date": _RUN_DATE,
         "indicator_name": "rsi",
         "raw_value": {"signal_value": 0.50, "rsi": 50.0},
         "normalized_value": 0.50, "direction": "neutral"},
    ])


@pytest.fixture()
def seeded_db() -> Storage:
    """In-memory Storage with 3 tickers seeded."""
    db = Storage(":memory:")
    _seed_storage(db)
    yield db
    db.close()


# ── Tests ────────────────────────────────────────────────────────────────────


class TestEnvelope:
    """Tests for the meta/envelope block."""

    def test_envelope_fields_present(self, seeded_db: Storage) -> None:
        result = build_dashboard_dict(
            seeded_db, _RUN_DATE, "sample", n_tickers_universe=100,
        )
        meta = result["meta"]
        assert meta["schema_version"] == "1.0"
        assert meta["run_date"] == "2024-06-15"
        assert meta["scope"] == "sample"
        assert meta["combination_name"] == "default"
        assert meta["n_tickers_scored"] == 3
        assert meta["n_tickers_universe"] == 100

    def test_envelope_generated_at_is_utc_iso(self, seeded_db: Storage) -> None:
        result = build_dashboard_dict(seeded_db, _RUN_DATE, "sample")
        ts = result["meta"]["generated_at"]
        # Must parse as ISO-8601 with timezone info
        parsed = datetime.fromisoformat(ts)
        assert parsed.tzinfo is not None

    def test_envelope_direction_counts(self, seeded_db: Storage) -> None:
        result = build_dashboard_dict(seeded_db, _RUN_DATE, "sample")
        meta = result["meta"]
        assert meta["n_buy"] == 1
        assert meta["n_sell"] == 1
        assert meta["n_neutral"] == 1

    def test_envelope_counts_match_tickers_length(self, seeded_db: Storage) -> None:
        result = build_dashboard_dict(seeded_db, _RUN_DATE, "sample")
        meta = result["meta"]
        assert (meta["n_buy"] + meta["n_sell"] + meta["n_neutral"]
                == meta["n_tickers_scored"]
                == len(result["tickers"]))


class TestRankedOrder:
    """Tickers must be sorted by rank_score descending."""

    def test_descending_rank_score(self, seeded_db: Storage) -> None:
        result = build_dashboard_dict(seeded_db, _RUN_DATE, "sample")
        scores = [t["rank_score"] for t in result["tickers"]]
        assert scores == sorted(scores, reverse=True)
        # MSFT (0.82) > AAPL (0.65) > XOM (0.30)
        assert result["tickers"][0]["ticker"] == "MSFT"
        assert result["tickers"][1]["ticker"] == "AAPL"
        assert result["tickers"][2]["ticker"] == "XOM"


class TestPerTickerFields:
    """Core per-ticker fields from tbl_combo_results."""

    def test_core_fields_present(self, seeded_db: Storage) -> None:
        result = build_dashboard_dict(seeded_db, _RUN_DATE, "sample")
        msft = result["tickers"][0]
        required = {
            "ticker", "exchange", "date", "direction",
            "combo_score", "rank_score", "agreement_count",
            "n_trade_indicators", "signals_firing",
            "vol_confirmation", "volume_confirmation",
            "days_since_breakout",
        }
        assert required <= set(msft.keys())

    def test_signals_firing_is_list_of_strings(self, seeded_db: Storage) -> None:
        result = build_dashboard_dict(seeded_db, _RUN_DATE, "sample")
        msft = result["tickers"][0]
        assert isinstance(msft["signals_firing"], list)
        assert all(isinstance(s, str) for s in msft["signals_firing"])
        assert "rsi" in msft["signals_firing"]

    def test_days_since_breakout_nullable(self, seeded_db: Storage) -> None:
        result = build_dashboard_dict(seeded_db, _RUN_DATE, "sample")
        xom = next(t for t in result["tickers"] if t["ticker"] == "XOM")
        assert xom["days_since_breakout"] is None

    def test_date_is_string(self, seeded_db: Storage) -> None:
        result = build_dashboard_dict(seeded_db, _RUN_DATE, "sample")
        assert result["tickers"][0]["date"] == "2024-06-15"


class TestUniverseMeta:
    """Per-ticker universe metadata block."""

    def test_meta_present_with_fields(self, seeded_db: Storage) -> None:
        result = build_dashboard_dict(seeded_db, _RUN_DATE, "sample")
        msft = result["tickers"][0]
        assert "meta" in msft
        meta = msft["meta"]
        assert meta["name"] == "Microsoft Corp."
        assert meta["sector"] == "Technology"
        assert meta["currency"] == "USD"

    def test_meta_null_tolerant(self) -> None:
        """Ticker without universe row gets all-null meta."""
        db = Storage(":memory:")
        # Write combo results for a ticker with NO universe entry
        combo_df = pd.DataFrame([{
            "ticker": "ORPHAN", "exchange": "XX", "date": _RUN_DATE,
            "combination_name": "default", "direction": "buy",
            "combo_score": 0.40, "rank_score": 0.50,
            "agreement_count": 3, "n_trade_indicators": 8,
            "signals_firing": json.dumps(["rsi"]),
            "vol_confirmation": "neutral", "volume_confirmation": "neutral",
            "days_since_breakout": None,
            "resolutions_available": 0, "resolutions_aligned": 0,
            "alignment_fraction": 0.0,
        }])
        db.write_combo_results(combo_df)
        result = build_dashboard_dict(db, _RUN_DATE, "sample")
        orphan = result["tickers"][0]
        assert orphan["meta"]["name"] is None
        assert orphan["meta"]["sector"] is None
        db.close()


class TestMtfAlignment:
    """MTF alignment block reads persisted values, never recomputes."""

    def test_mtf_alignment_present(self, seeded_db: Storage) -> None:
        result = build_dashboard_dict(seeded_db, _RUN_DATE, "sample")
        msft = result["tickers"][0]
        assert "mtf_alignment" in msft
        mtf = msft["mtf_alignment"]
        assert mtf["resolutions_available"] == 2
        assert mtf["resolutions_aligned"] == 2
        assert mtf["alignment_fraction"] == pytest.approx(1.0)

    def test_mtf_alignment_partial(self, seeded_db: Storage) -> None:
        result = build_dashboard_dict(seeded_db, _RUN_DATE, "sample")
        aapl = next(t for t in result["tickers"] if t["ticker"] == "AAPL")
        mtf = aapl["mtf_alignment"]
        assert mtf["resolutions_available"] == 2
        assert mtf["resolutions_aligned"] == 1
        assert mtf["alignment_fraction"] == pytest.approx(0.5)

    def test_mtf_alignment_zero(self, seeded_db: Storage) -> None:
        result = build_dashboard_dict(seeded_db, _RUN_DATE, "sample")
        xom = next(t for t in result["tickers"] if t["ticker"] == "XOM")
        mtf = xom["mtf_alignment"]
        assert mtf["resolutions_available"] == 0
        assert mtf["resolutions_aligned"] == 0
        assert mtf["alignment_fraction"] == pytest.approx(0.0)


class TestIndicatorDetail:
    """Indicator detail tiering: full for non-neutral, summary for neutral."""

    def test_non_neutral_has_raw_value(self, seeded_db: Storage) -> None:
        result = build_dashboard_dict(seeded_db, _RUN_DATE, "sample")
        msft = result["tickers"][0]
        assert "indicators" in msft
        rsi = msft["indicators"]["rsi"]
        assert "raw_value" in rsi
        assert rsi["raw_value"]["rsi"] == pytest.approx(30.0)
        assert rsi["direction"] == "buy"
        assert rsi["normalized_value"] == pytest.approx(0.30)

    def test_neutral_has_summary_only(self, seeded_db: Storage) -> None:
        result = build_dashboard_dict(seeded_db, _RUN_DATE, "sample")
        xom = next(t for t in result["tickers"] if t["ticker"] == "XOM")
        rsi = xom["indicators"]["rsi"]
        assert "raw_value" not in rsi
        assert rsi["direction"] == "neutral"
        assert "normalized_value" in rsi

    def test_mtf_flat_keys_in_indicators(self, seeded_db: Storage) -> None:
        """box_breakout_weekly / _monthly appear as flat keys."""
        result = build_dashboard_dict(seeded_db, _RUN_DATE, "sample")
        msft = result["tickers"][0]
        inds = msft["indicators"]
        assert "box_breakout" in inds
        assert "box_breakout_weekly" in inds
        assert "box_breakout_monthly" in inds
        # Weekly should have raw_value since MSFT is non-neutral
        assert "raw_value" in inds["box_breakout_weekly"]


class TestEmptyRun:
    """Empty run (no tickers scored) must produce valid JSON."""

    def test_empty_run(self) -> None:
        db = Storage(":memory:")
        result = build_dashboard_dict(db, _RUN_DATE, "sample")
        assert result["meta"]["n_tickers_scored"] == 0
        assert result["meta"]["n_buy"] == 0
        assert result["meta"]["n_sell"] == 0
        assert result["meta"]["n_neutral"] == 0
        assert result["tickers"] == []
        db.close()


class TestRoundTrip:
    """JSON serialization and deserialization round-trip."""

    def test_json_round_trip(self, seeded_db: Storage, tmp_path: Path) -> None:
        data = build_dashboard_dict(seeded_db, _RUN_DATE, "sample")
        out = write_dashboard_json(data, tmp_path / "out.json")
        assert out.exists()
        with open(out) as f:
            loaded = json.load(f)
        assert loaded["meta"]["schema_version"] == "1.0"
        assert len(loaded["tickers"]) == 3
        # Scores survive round-trip
        assert loaded["tickers"][0]["rank_score"] == pytest.approx(0.82)

    def test_write_creates_parent_dirs(self, seeded_db: Storage, tmp_path: Path) -> None:
        data = build_dashboard_dict(seeded_db, _RUN_DATE, "sample")
        nested = tmp_path / "a" / "b" / "out.json"
        out = write_dashboard_json(data, nested)
        assert out.exists()

    def test_write_returns_path(self, seeded_db: Storage, tmp_path: Path) -> None:
        data = build_dashboard_dict(seeded_db, _RUN_DATE, "sample")
        out = write_dashboard_json(data, tmp_path / "out.json")
        assert isinstance(out, Path)


class TestStorageReaderAll:
    """Test that read_combo_results_all works correctly."""

    def test_returns_all_tickers_for_date(self, seeded_db: Storage) -> None:
        df = seeded_db.read_combo_results_all(_RUN_DATE, "default")
        assert len(df) == 3
        assert set(df["ticker"]) == {"AAPL", "MSFT", "XOM"}

    def test_empty_for_wrong_date(self, seeded_db: Storage) -> None:
        df = seeded_db.read_combo_results_all(date(2000, 1, 1), "default")
        assert len(df) == 0

    def test_empty_for_wrong_combination(self, seeded_db: Storage) -> None:
        df = seeded_db.read_combo_results_all(_RUN_DATE, "nonexistent")
        assert len(df) == 0
