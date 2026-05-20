"""Tests for src/scanner/data/storage.py."""

from __future__ import annotations

import json
from datetime import date

import duckdb
import pandas as pd
import pytest

from scanner.data.storage import Storage

# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def db() -> Storage:
    """In-memory Storage instance, closed after each test."""
    with Storage(":memory:") as s:
        yield s


def _prices_df(n: int = 5, start: str = "2024-01-01") -> pd.DataFrame:
    dates = pd.date_range(start, periods=n, freq="B")
    return pd.DataFrame({
        "date":      dates,
        "open":      [100.0 + i for i in range(n)],
        "high":      [102.0 + i for i in range(n)],
        "low":       [99.0  + i for i in range(n)],
        "close":     [101.0 + i for i in range(n)],
        "adj_close": [101.0 + i for i in range(n)],
        "volume":    [1_000_000 + i * 10_000 for i in range(n)],
    })


def _universe_df() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "ticker": "AAPL", "exchange": "US", "name": "Apple Inc.",
            "currency": "USD", "market_cap_usd": 3e12, "sector": "Technology",
            "region": "North America",
        },
        {
            "ticker": "MSFT", "exchange": "US", "name": "Microsoft Corp.",
            "currency": "USD", "market_cap_usd": 2.8e12, "sector": "Technology",
            "region": "North America",
        },
    ])


# ── Schema / instantiation ────────────────────────────────────────────────────

def test_schema_tables_created(db: Storage) -> None:
    """All five tables must exist after __init__."""
    tables = {
        row[0]
        for row in db._con.execute("SHOW TABLES").fetchall()
    }
    assert tables == {
        "tbl_universe",
        "tbl_prices",
        "tbl_indicator_outputs",
        "tbl_combo_results",
        "tbl_run_log",
    }


def test_context_manager_closes() -> None:
    """__exit__ must close the connection without raising."""
    s = Storage(":memory:")
    with s:
        pass
    # After close, further queries should raise
    with pytest.raises(duckdb.Error):
        s._con.execute("SELECT 1")


# ── Universe ──────────────────────────────────────────────────────────────────

def test_write_read_universe(db: Storage) -> None:
    df = _universe_df()
    db.write_universe(df)
    out = db.read_universe()
    assert set(out["ticker"]) == {"AAPL", "MSFT"}
    assert set(out.columns) >= {"ticker", "exchange", "name", "sector"}


def test_universe_upsert_no_duplication(db: Storage) -> None:
    df = _universe_df()
    db.write_universe(df)
    db.write_universe(df)  # second write must not duplicate
    out = db.read_universe()
    assert len(out) == 2


def test_universe_upsert_updates_row(db: Storage) -> None:
    df = _universe_df()
    db.write_universe(df)
    updated = df.copy()
    updated.loc[updated["ticker"] == "AAPL", "market_cap_usd"] = 1.0
    db.write_universe(updated)
    out = db.read_universe()
    aapl = out[out["ticker"] == "AAPL"].iloc[0]
    assert aapl["market_cap_usd"] == 1.0
    assert len(out) == 2


def test_universe_ordered_by_exchange_ticker(db: Storage) -> None:
    df = _universe_df()
    db.write_universe(df)
    out = db.read_universe()
    tickers = list(out["ticker"])
    assert tickers == sorted(tickers)


# ── Prices ────────────────────────────────────────────────────────────────────

def test_write_read_prices(db: Storage) -> None:
    df = _prices_df()
    db.write_prices("AAPL", "US", df)
    out = db.read_prices("AAPL", "US")
    assert len(out) == 5
    assert list(out.columns[:6]) == ["date", "open", "high", "low", "close", "adj_close"]


def test_prices_ascending_date_order(db: Storage) -> None:
    df = _prices_df()
    db.write_prices("AAPL", "US", df)
    out = db.read_prices("AAPL", "US")
    dates = list(out["date"])
    assert dates == sorted(dates)


def test_prices_idempotent(db: Storage) -> None:
    df = _prices_df()
    db.write_prices("AAPL", "US", df)
    db.write_prices("AAPL", "US", df)  # same data twice
    out = db.read_prices("AAPL", "US")
    assert len(out) == 5


def test_prices_upsert_updates_close(db: Storage) -> None:
    df = _prices_df(n=1)
    db.write_prices("AAPL", "US", df)
    updated = df.copy()
    updated["close"] = 999.0
    db.write_prices("AAPL", "US", updated)
    out = db.read_prices("AAPL", "US")
    assert float(out["close"].iloc[0]) == 999.0


def test_prices_isolated_by_ticker(db: Storage) -> None:
    db.write_prices("AAPL", "US", _prices_df(n=3))
    db.write_prices("MSFT", "US", _prices_df(n=7))
    assert len(db.read_prices("AAPL", "US")) == 3
    assert len(db.read_prices("MSFT", "US")) == 7


def test_prices_default_source(db: Storage) -> None:
    db.write_prices("AAPL", "US", _prices_df(n=1))
    out = db.read_prices("AAPL", "US")
    assert out["source"].iloc[0] == "eodhd"


def test_prices_custom_source(db: Storage) -> None:
    db.write_prices("AAPL", "US", _prices_df(n=1), source="yfinance")
    out = db.read_prices("AAPL", "US")
    assert out["source"].iloc[0] == "yfinance"


# ── Indicator outputs (Layer 1) ───────────────────────────────────────────────

def _indicator_row(
    ticker: str = "AAPL",
    exchange: str = "US",
    dt: date = date(2024, 1, 5),
    indicator_name: str = "rsi",
    raw_value: dict | None = None,
    normalized_value: float | None = 0.6,
    direction: str | None = "buy",
) -> dict:
    return {
        "ticker":           ticker,
        "exchange":         exchange,
        "date":             dt,
        "indicator_name":   indicator_name,
        "raw_value":        raw_value or {"signal_value": 0.6, "rsi": 35.2},
        "normalized_value": normalized_value,
        "direction":        direction,
    }


def test_write_read_indicator_outputs(db: Storage) -> None:
    row = _indicator_row()
    db.write_indicator_outputs([row])
    out = db.read_indicator_outputs("AAPL", "US", date(2024, 1, 5))
    assert "rsi" in out
    assert out["rsi"]["direction"] == "buy"


def test_indicator_raw_value_json_roundtrip(db: Storage) -> None:
    raw = {"signal_value": 0.6, "rsi": 35.2, "nested": {"a": 1}}
    row = _indicator_row(raw_value=raw)
    db.write_indicator_outputs([row])
    out = db.read_indicator_outputs("AAPL", "US", date(2024, 1, 5))
    assert out["rsi"]["raw_value"] == raw


def test_indicator_outputs_idempotent(db: Storage) -> None:
    row = _indicator_row()
    db.write_indicator_outputs([row])
    db.write_indicator_outputs([row])
    out = db.read_indicator_outputs("AAPL", "US", date(2024, 1, 5))
    assert len(out) == 1


def test_indicator_outputs_upsert_updates_value(db: Storage) -> None:
    row = _indicator_row(normalized_value=0.6)
    db.write_indicator_outputs([row])
    updated = _indicator_row(normalized_value=0.9)
    db.write_indicator_outputs([updated])
    out = db.read_indicator_outputs("AAPL", "US", date(2024, 1, 5))
    assert out["rsi"]["normalized_value"] == pytest.approx(0.9)


def test_indicator_outputs_nullable_normalized_value(db: Storage) -> None:
    """mav_diff_z has no normalized_value — must be stored and returned as None."""
    row = _indicator_row(
        indicator_name="mav_diff_z",
        raw_value={"z_score": 1.5, "mav_diff": 3.0},
        normalized_value=None,
        direction=None,
    )
    db.write_indicator_outputs([row])
    out = db.read_indicator_outputs("AAPL", "US", date(2024, 1, 5))
    assert out["mav_diff_z"]["normalized_value"] is None
    assert out["mav_diff_z"]["direction"] is None


def test_indicator_outputs_multiple_indicators(db: Storage) -> None:
    rows = [
        _indicator_row(indicator_name="rsi"),
        _indicator_row(indicator_name="bollinger_normal",
                       raw_value={"z_score": 1.6}, direction="buy"),
        _indicator_row(indicator_name="stochastic",
                       raw_value={"stoch_k": 15.0}, direction="buy"),
    ]
    db.write_indicator_outputs(rows)
    out = db.read_indicator_outputs("AAPL", "US", date(2024, 1, 5))
    assert set(out.keys()) == {"rsi", "bollinger_normal", "stochastic"}


def test_indicator_outputs_isolated_by_date(db: Storage) -> None:
    db.write_indicator_outputs([_indicator_row(dt=date(2024, 1, 4))])
    db.write_indicator_outputs([_indicator_row(dt=date(2024, 1, 5))])
    out4 = db.read_indicator_outputs("AAPL", "US", date(2024, 1, 4))
    out5 = db.read_indicator_outputs("AAPL", "US", date(2024, 1, 5))
    assert len(out4) == 1
    assert len(out5) == 1


def test_indicator_outputs_raw_value_none(db: Storage) -> None:
    row = _indicator_row(raw_value=None)
    # raw_value=None case: store None, retrieve None
    rows_to_store = [row]
    rows_to_store[0] = dict(row, raw_value=None)
    db.write_indicator_outputs(rows_to_store)
    out = db.read_indicator_outputs("AAPL", "US", date(2024, 1, 5))
    assert out["rsi"]["raw_value"] is None


def test_write_indicator_outputs_empty_noop(db: Storage) -> None:
    db.write_indicator_outputs([])  # must not raise
    out = db.read_indicator_outputs("AAPL", "US", date(2024, 1, 5))
    assert out == {}


# ── Combo results (Layer 2) ───────────────────────────────────────────────────

def _combo_df() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "ticker":             "AAPL",
            "exchange":           "US",
            "date":               date(2024, 1, 5),
            "combination_name":   "default",
            "direction":          "buy",
            "combo_score":        0.72,
            "rank_score":         0.68,
            "agreement_count":    5,
            "n_trade_indicators": 8,
            "signals_firing":     ["rsi", "bollinger_normal", "stochastic"],
            "vol_confirmation":   "confirm",
            "volume_confirmation": "confirm",
            "days_since_breakout": 3,
        }
    ])


def test_write_read_combo_results(db: Storage) -> None:
    db.write_combo_results(_combo_df())
    out = db.read_combo_results("AAPL", "US", date(2024, 1, 5))
    assert len(out) == 1
    assert out["combination_name"].iloc[0] == "default"
    assert float(out["combo_score"].iloc[0]) == pytest.approx(0.72)


def test_combo_signals_firing_json_roundtrip(db: Storage) -> None:
    db.write_combo_results(_combo_df())
    out = db.read_combo_results("AAPL", "US", date(2024, 1, 5))
    raw = out["signals_firing"].iloc[0]
    parsed = json.loads(raw)
    assert parsed == ["rsi", "bollinger_normal", "stochastic"]


def test_combo_results_idempotent(db: Storage) -> None:
    db.write_combo_results(_combo_df())
    db.write_combo_results(_combo_df())
    out = db.read_combo_results("AAPL", "US", date(2024, 1, 5))
    assert len(out) == 1


def test_combo_results_upsert_updates_score(db: Storage) -> None:
    db.write_combo_results(_combo_df())
    updated = _combo_df()
    updated["combo_score"] = 0.99
    db.write_combo_results(updated)
    out = db.read_combo_results("AAPL", "US", date(2024, 1, 5))
    assert float(out["combo_score"].iloc[0]) == pytest.approx(0.99)


def test_combo_results_isolated_by_date(db: Storage) -> None:
    df1 = _combo_df()
    df2 = _combo_df()
    df2["date"] = date(2024, 1, 8)
    db.write_combo_results(df1)
    db.write_combo_results(df2)
    out5 = db.read_combo_results("AAPL", "US", date(2024, 1, 5))
    out8 = db.read_combo_results("AAPL", "US", date(2024, 1, 8))
    assert len(out5) == 1
    assert len(out8) == 1


def test_combo_results_signals_firing_already_json_string(db: Storage) -> None:
    """write_combo_results must handle pre-serialised JSON strings without double-encoding."""
    df = _combo_df()
    df["signals_firing"] = df["signals_firing"].apply(json.dumps)
    db.write_combo_results(df)
    out = db.read_combo_results("AAPL", "US", date(2024, 1, 5))
    raw = out["signals_firing"].iloc[0]
    parsed = json.loads(raw)
    assert parsed == ["rsi", "bollinger_normal", "stochastic"]


# ── Run log ───────────────────────────────────────────────────────────────────

def test_log_run_lifecycle(db: Storage) -> None:
    run_id = "run-2024-01-05"
    db.log_run_start(run_id, date(2024, 1, 5), "sample")
    db.log_run_ticker_done(run_id, "AAPL", "US")
    db.log_run_ticker_done(run_id, "MSFT", "US")
    db.log_run_end(run_id, "completed", api_calls=18)

    row = db._con.execute(
        "SELECT status, api_calls_used FROM tbl_run_log WHERE run_id = ?", [run_id]
    ).fetchone()
    assert row[0] == "completed"
    assert row[1] == 18


def test_get_completed_tickers(db: Storage) -> None:
    run_id = "run-2024-01-05"
    db.log_run_start(run_id, date(2024, 1, 5), "sample")
    db.log_run_ticker_done(run_id, "AAPL", "US")
    db.log_run_ticker_done(run_id, "MSFT", "US")

    done = db.get_completed_tickers(run_id)
    assert done == {("AAPL", "US"), ("MSFT", "US")}


def test_get_completed_tickers_unknown_run_id(db: Storage) -> None:
    done = db.get_completed_tickers("nonexistent-run")
    assert done == set()


def test_log_run_start_idempotent(db: Storage) -> None:
    """Second log_run_start for the same run_id must be a no-op."""
    run_id = "run-2024-01-05"
    db.log_run_start(run_id, date(2024, 1, 5), "sample")
    db.log_run_ticker_done(run_id, "AAPL", "US")
    db.log_run_start(run_id, date(2024, 1, 5), "sample")  # no-op
    done = db.get_completed_tickers(run_id)
    # tickers_done must not have been reset by the second log_run_start
    assert ("AAPL", "US") in done


def test_log_run_ticker_done_unknown_run_id_noop(db: Storage) -> None:
    """ticker_done for unknown run_id must not raise."""
    db.log_run_ticker_done("no-such-run", "AAPL", "US")  # must not raise


def test_log_run_end_updates_status(db: Storage) -> None:
    run_id = "run-x"
    db.log_run_start(run_id, date(2024, 1, 5), "us")
    db.log_run_end(run_id, "failed", api_calls=5)
    row = db._con.execute(
        "SELECT status FROM tbl_run_log WHERE run_id = ?", [run_id]
    ).fetchone()
    assert row[0] == "failed"
