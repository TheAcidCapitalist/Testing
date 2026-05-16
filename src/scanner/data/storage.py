"""DuckDB storage layer for signal-scanner.

Two-layer persistence per spec/scoring.md §"Per-indicator storage":
  Layer 1 — tbl_indicator_outputs: per-indicator outputs keyed by
             (ticker, exchange, date, indicator_name).  Source of truth;
             subset re-combination is a query over these rows.
  Layer 2 — tbl_combo_results: derived combo + ranking rows keyed by
             (ticker, exchange, date, combination_name).  Always
             recomputeable from Layer 1.

Also stores raw OHLCV (tbl_prices), universe metadata (tbl_universe),
and run-log rows (tbl_run_log) for idempotent orchestration.

All writes are upserts (INSERT OR REPLACE on the primary key).
A re-run of the same day's data never duplicates rows.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

_DEFAULT_DB = Path("data/scanner.duckdb")


class Storage:
    """DuckDB-backed store for the signal-scanner data pipeline.

    Parameters
    ----------
    path:
        Path to the DuckDB file, or ``":memory:"`` for an in-process
        in-memory database (used by tests).
    """

    def __init__(self, path: str | Path = _DEFAULT_DB) -> None:
        db_path = str(path)
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(db_path)
        self._init_schema()

    # ── Schema ───────────────────────────────────────────────────────────────

    def _init_schema(self) -> None:
        self._con.execute("""
            CREATE TABLE IF NOT EXISTS tbl_universe (
                ticker          VARCHAR NOT NULL,
                exchange        VARCHAR NOT NULL,
                name            VARCHAR,
                currency        VARCHAR,
                market_cap_usd  DOUBLE,
                sector          VARCHAR,
                region          VARCHAR,
                updated_at      TIMESTAMP DEFAULT current_timestamp,
                PRIMARY KEY (ticker, exchange)
            )
        """)
        self._con.execute("""
            CREATE TABLE IF NOT EXISTS tbl_prices (
                ticker     VARCHAR NOT NULL,
                exchange   VARCHAR NOT NULL,
                date       DATE    NOT NULL,
                open       DOUBLE,
                high       DOUBLE,
                low        DOUBLE,
                close      DOUBLE,
                adj_close  DOUBLE,
                volume     BIGINT,
                source     VARCHAR,
                PRIMARY KEY (ticker, exchange, date)
            )
        """)
        # Layer 1: per-indicator source of truth
        self._con.execute("""
            CREATE TABLE IF NOT EXISTS tbl_indicator_outputs (
                ticker           VARCHAR NOT NULL,
                exchange         VARCHAR NOT NULL,
                date             DATE    NOT NULL,
                indicator_name   VARCHAR NOT NULL,
                raw_value        VARCHAR,  -- JSON: full compute() dict
                normalized_value DOUBLE,   -- Stage-1 score 0-1; null for mav_diff_z
                direction        VARCHAR,  -- 'buy'|'sell'|'neutral'|null
                PRIMARY KEY (ticker, exchange, date, indicator_name)
            )
        """)
        # Layer 2: derived combo + ranking (recomputeable from layer 1)
        self._con.execute("""
            CREATE TABLE IF NOT EXISTS tbl_combo_results (
                ticker                VARCHAR NOT NULL,
                exchange              VARCHAR NOT NULL,
                date                  DATE    NOT NULL,
                combination_name      VARCHAR NOT NULL,
                direction             VARCHAR,
                combo_score           DOUBLE,
                rank_score            DOUBLE,
                agreement_count       INTEGER,
                n_trade_indicators    INTEGER,
                signals_firing        VARCHAR,  -- JSON list of indicator names
                vol_confirmation      VARCHAR,
                volume_confirmation   VARCHAR,
                days_since_breakout   INTEGER,
                PRIMARY KEY (ticker, exchange, date, combination_name)
            )
        """)
        # Run log for idempotent orchestration and budget tracking
        self._con.execute("""
            CREATE TABLE IF NOT EXISTS tbl_run_log (
                run_id         VARCHAR NOT NULL,
                run_date       DATE,
                scope          VARCHAR,
                status         VARCHAR,      -- 'started'|'completed'|'failed'|'partial'
                tickers_done   VARCHAR DEFAULT '[]',  -- JSON [[ticker, exchange], ...]
                api_calls_used INTEGER DEFAULT 0,
                started_at     TIMESTAMP,
                finished_at    TIMESTAMP,
                PRIMARY KEY (run_id)
            )
        """)

    # ── Universe ─────────────────────────────────────────────────────────────

    def write_universe(self, df: pd.DataFrame) -> None:
        """Upsert universe metadata rows.

        df must contain columns: ticker, exchange, name, currency,
        market_cap_usd, sector, region.
        """
        _df = df[["ticker", "exchange", "name", "currency",
                  "market_cap_usd", "sector", "region"]].copy()
        _df["updated_at"] = pd.Timestamp.now()
        self._con.execute("INSERT OR REPLACE INTO tbl_universe SELECT * FROM _df")

    def read_universe(self) -> pd.DataFrame:
        """Return all universe rows ordered by exchange, ticker."""
        return self._con.execute(
            "SELECT * FROM tbl_universe ORDER BY exchange, ticker"
        ).df()

    # ── OHLCV prices ─────────────────────────────────────────────────────────

    def write_prices(
        self,
        ticker: str,
        exchange: str,
        df: pd.DataFrame,
        source: str = "eodhd",
    ) -> None:
        """Upsert OHLCV rows for one (ticker, exchange).

        df must contain columns: date, open, high, low, close, adj_close, volume.
        Existing rows for the same (ticker, exchange, date) are replaced.
        """
        _df = df[["date", "open", "high", "low", "close", "adj_close", "volume"]].copy()
        _df.insert(0, "ticker", ticker)
        _df.insert(1, "exchange", exchange)
        _df["source"] = source
        _df["volume"] = _df["volume"].astype("Int64")  # nullable int; tolerates NaN
        self._con.execute("INSERT OR REPLACE INTO tbl_prices SELECT * FROM _df")

    def read_prices(self, ticker: str, exchange: str) -> pd.DataFrame:
        """Return OHLCV rows for one (ticker, exchange), ascending by date."""
        return self._con.execute(
            """SELECT date, open, high, low, close, adj_close, volume, source
               FROM tbl_prices
               WHERE ticker = ? AND exchange = ?
               ORDER BY date""",
            [ticker, exchange],
        ).df()

    # ── Indicator outputs (Layer 1) ───────────────────────────────────────────

    def write_indicator_outputs(self, rows: list[dict]) -> None:
        """Upsert per-indicator compute() results.

        Each dict must contain:
            ticker, exchange, date, indicator_name,
            raw_value (dict — serialised to JSON),
            normalized_value (float | None),
            direction (str | None).
        """
        if not rows:
            return
        records = []
        for r in rows:
            rv = r.get("raw_value")
            records.append({
                "ticker":           r["ticker"],
                "exchange":         r["exchange"],
                "date":             r["date"],
                "indicator_name":   r["indicator_name"],
                "raw_value":        json.dumps(rv) if isinstance(rv, dict) else rv,
                "normalized_value": r.get("normalized_value"),
                "direction":        r.get("direction"),
            })
        _df = pd.DataFrame(records)
        self._con.execute(
            "INSERT OR REPLACE INTO tbl_indicator_outputs SELECT * FROM _df"
        )

    def read_indicator_outputs(
        self,
        ticker: str,
        exchange: str,
        as_of_date: date | str,
    ) -> dict[str, dict]:
        """Return indicator outputs for one (ticker, exchange, date).

        Returns {indicator_name: {raw_value, normalized_value, direction}}.
        raw_value is a dict (deserialised from JSON), or None.
        """
        rows = self._con.execute(
            """SELECT indicator_name, raw_value, normalized_value, direction
               FROM tbl_indicator_outputs
               WHERE ticker = ? AND exchange = ? AND date = ?""",
            [ticker, exchange, as_of_date],
        ).fetchall()
        return {
            row[0]: {
                "raw_value":        json.loads(row[1]) if row[1] is not None else None,
                "normalized_value": row[2],
                "direction":        row[3],
            }
            for row in rows
        }

    # ── Combo results (Layer 2) ───────────────────────────────────────────────

    def write_combo_results(self, df: pd.DataFrame) -> None:
        """Upsert combo + ranking rows.

        df must contain the columns of tbl_combo_results.
        signals_firing may be a Python list — it is JSON-serialised automatically.
        """
        _df = df.copy()
        if "signals_firing" in _df.columns:
            _df["signals_firing"] = _df["signals_firing"].apply(
                lambda x: json.dumps(x) if isinstance(x, list) else x
            )
        self._con.execute(
            "INSERT OR REPLACE INTO tbl_combo_results SELECT * FROM _df"
        )

    def read_combo_results(
        self,
        ticker: str,
        exchange: str,
        as_of_date: date | str,
    ) -> pd.DataFrame:
        """Return all combination rows for one (ticker, exchange, date)."""
        return self._con.execute(
            """SELECT * FROM tbl_combo_results
               WHERE ticker = ? AND exchange = ? AND date = ?
               ORDER BY combination_name""",
            [ticker, exchange, as_of_date],
        ).df()

    # ── Run log ───────────────────────────────────────────────────────────────

    def log_run_start(self, run_id: str, run_date: date, scope: str) -> None:
        """Record the start of a run.  Safe to call more than once for the same
        run_id — subsequent calls are no-ops (ON CONFLICT DO NOTHING)."""
        self._con.execute(
            """INSERT INTO tbl_run_log
               (run_id, run_date, scope, status, tickers_done, api_calls_used, started_at)
               VALUES (?, ?, ?, 'started', '[]', 0, current_timestamp)
               ON CONFLICT (run_id) DO NOTHING""",
            [run_id, run_date, scope],
        )

    def log_run_ticker_done(self, run_id: str, ticker: str, exchange: str) -> None:
        """Append one (ticker, exchange) to this run's completed-tickers list."""
        row = self._con.execute(
            "SELECT tickers_done FROM tbl_run_log WHERE run_id = ?", [run_id]
        ).fetchone()
        if row is None:
            return
        done: list = json.loads(row[0] or "[]")
        done.append([ticker, exchange])
        self._con.execute(
            "UPDATE tbl_run_log SET tickers_done = ? WHERE run_id = ?",
            [json.dumps(done), run_id],
        )

    def log_run_end(self, run_id: str, status: str, api_calls: int) -> None:
        """Mark a run finished and record the total API call count."""
        self._con.execute(
            """UPDATE tbl_run_log
               SET status = ?, api_calls_used = ?, finished_at = current_timestamp
               WHERE run_id = ?""",
            [status, api_calls, run_id],
        )

    def get_completed_tickers(self, run_id: str) -> set[tuple[str, str]]:
        """Return {(ticker, exchange)} for all tickers completed in this run.

        Used by the orchestrator to skip already-processed tickers on re-run.
        Returns an empty set if run_id is unknown.
        """
        row = self._con.execute(
            "SELECT tickers_done FROM tbl_run_log WHERE run_id = ?", [run_id]
        ).fetchone()
        if row is None:
            return set()
        return {(pair[0], pair[1]) for pair in json.loads(row[0] or "[]")}

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def close(self) -> None:
        self._con.close()

    def __enter__(self) -> Storage:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
