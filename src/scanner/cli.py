"""CLI entry-point and daily-scan orchestrator for signal-scanner.

Usage
-----
    uv run scanner run-daily --universe sample

The ``run-daily`` command implements the full daily scan pipeline:

  1. Load the candidate universe (Stage 1).
  2. Per-ticker fetch loop — budget-aware and fetch-idempotent:
       * Skip any ticker whose today's bar is already in storage.
       * Fetch via EODHD; store immediately.
       * On DailyBudgetExceeded: stop fetching, continue with stored data.
       * On any other fetch error (404, 5xx, network): log and skip; no retry.
  3. Apply post-ingestion universe filters (Stage 2).
  4. Run all registered indicators over each surviving ticker's stored OHLCV.
  5. Compute combo score + ranking (default combination).
  6. Write both storage layers; emit a minimal verification dump.

Constraints (load-bearing):
  * Budget-aware loop — DailyBudgetExceeded stops fetching but not the run.
  * Fetch idempotency — today's bar already stored → skip fetch.
  * No retry within a run — a failed fetch is logged and skipped.
  * Single-ticker failure must not crash the run.
  * Daily resolution only — multi-timeframe is a v2 upgrade.
  * No report, email, or LLM — Phase D.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from scanner.data.eodhd import (
    CallBudget,
    DailyBudgetExceeded,
    EODHDAuthError,
    EODHDClient,
    EODHDError,
)
from scanner.data.storage import Storage
from scanner.data.universe import apply_post_ingest_filters, candidates
from scanner.indicators import REGISTRY
from scanner.scoring import normalize, score_tickers

logger = logging.getLogger(__name__)

_DEFAULT_DB = Path("data/scanner.duckdb")
_DEFAULT_DAILY_LIMIT = 5000


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run_daily(
    scope: str = "sample",
    *,
    db_path: str | Path = _DEFAULT_DB,
    client: EODHDClient | None = None,
    run_date: date | None = None,
    daily_budget_limit: int = _DEFAULT_DAILY_LIMIT,
    output_path: str | None = None,
) -> dict:
    """Orchestrate the daily scan for the given universe scope.

    Parameters
    ----------
    scope:
        Universe scope: ``"sample"``, ``"us"``, or ``"global"``.
        ``"us"`` and ``"global"`` are gated behind ``ProductionScopeUnavailable``.
    db_path:
        Path to the DuckDB database file.  Pass ``":memory:"`` for in-process
        testing.
    client:
        Injected :class:`~scanner.data.eodhd.EODHDClient`.  If ``None``, a
        live client is constructed from the environment (requires
        ``EODHD_API_KEY`` in ``.env``).
    run_date:
        Override today's date (for testing / backfill).  Defaults to
        ``date.today()``.
    daily_budget_limit:
        Maximum EODHD API calls for this run.  Default 5000 (runaway protection).
    output_path:
        If set, write the ranked results CSV to this path.  If ``None``,
        print the top-10 rows to stdout.

    Returns
    -------
    dict with keys:
        ``fetched`` (int), ``skipped_idempotent`` (int), ``failed`` (int),
        ``budget_exhausted`` (bool), ``survivors`` (int), ``ranked`` (int),
        ``status`` (str: "completed" | "partial").
    """
    effective_date = run_date or date.today()
    run_id = f"{effective_date}_{scope}"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )

    with Storage(db_path) as storage:
        # ── Start run log ──────────────────────────────────────────────────
        storage.log_run_start(run_id, effective_date, scope)

        # ── Stage 1: load candidate universe ──────────────────────────────
        candidates_df = candidates(scope)
        logger.info(
            "Universe '%s': %d candidates loaded.", scope, len(candidates_df)
        )
        storage.write_universe(candidates_df)

        # ── Build budget + client ──────────────────────────────────────────
        budget = CallBudget(storage, run_id, daily_limit=daily_budget_limit)
        if client is None:
            client = EODHDClient(budget)

        # ── Per-ticker fetch loop ──────────────────────────────────────────
        budget_exhausted = False
        n_fetched = 0
        n_skipped_idempotent = 0
        n_failed = 0

        for _, row in candidates_df.iterrows():
            ticker: str = row["ticker"]
            exchange: str = row["exchange"]
            eodhd_ticker = f"{ticker}.{exchange}"

            # Fetch idempotency: skip if today's bar is already stored.
            existing = storage.read_prices(ticker, exchange)
            if not existing.empty:
                latest_stored = pd.Timestamp(existing["date"].max()).date()
                if latest_stored >= effective_date:
                    logger.info(
                        "[%s] today's bar already stored (%s) — skipping fetch.",
                        ticker, latest_stored,
                    )
                    n_skipped_idempotent += 1
                    continue

            if budget_exhausted:
                logger.debug("[%s] budget exhausted — skipping.", ticker)
                continue

            try:
                price_df = client.fetch_eod(eodhd_ticker)
                storage.write_prices(ticker, exchange, price_df)
                storage.log_run_ticker_done(run_id, ticker, exchange)
                logger.info("[%s] fetched %d bars.", ticker, len(price_df))
                n_fetched += 1

            except DailyBudgetExceeded as exc:
                logger.warning(
                    "Daily API budget exhausted after %d calls (%s). "
                    "Stopping fetch loop; continuing with stored data.",
                    budget.used, exc,
                )
                budget_exhausted = True

            except EODHDAuthError:
                # Auth failure is fatal — no point continuing.
                logger.error(
                    "[%s] Authentication error — check EODHD_API_KEY. Aborting.",
                    ticker,
                )
                storage.log_run_end(run_id, "failed", budget.used)
                raise

            except EODHDError as exc:
                # 404, 5xx, network, throttle — log and skip; no retry.
                logger.warning("[%s] fetch failed: %s — skipping.", ticker, exc)
                n_failed += 1

            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[%s] unexpected error during fetch: %s — skipping.",
                    ticker, exc,
                )
                n_failed += 1

        logger.info(
            "Fetch loop complete: fetched=%d  idempotent_skip=%d  failed=%d  "
            "budget_exhausted=%s  calls_used=%d/%d.",
            n_fetched, n_skipped_idempotent, n_failed,
            budget_exhausted, budget.used, daily_budget_limit,
        )

        # ── Stage 2: post-ingestion universe filters ───────────────────────
        survivors_df = apply_post_ingest_filters(candidates_df, storage)
        logger.info(
            "Post-ingest filters: %d/%d candidates survived.",
            len(survivors_df), len(candidates_df),
        )

        # ── Run indicators over survivors ──────────────────────────────────
        indicator_outputs: dict[tuple[str, str], dict[str, dict]] = {}

        for _, row in survivors_df.iterrows():
            ticker = row["ticker"]
            exchange = row["exchange"]

            prices = storage.read_prices(ticker, exchange)
            if prices.empty:
                logger.warning("[%s] no prices in storage — skipping indicators.", ticker)
                continue

            ticker_raw: dict[str, dict] = {}
            indicator_rows: list[dict] = []

            for ind_name, mod in REGISTRY.items():
                try:
                    raw = mod.compute(prices)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[%s] indicator '%s' failed: %s — skipping.",
                        ticker, ind_name, exc,
                    )
                    continue

                norm_val = normalize(ind_name, raw)
                # direction: trade indicators have "direction"; confirmation
                # indicators have "state".  Prefer "direction".
                direction = raw.get("direction") or raw.get("state")

                indicator_rows.append({
                    "ticker":           ticker,
                    "exchange":         exchange,
                    "date":             effective_date,
                    "indicator_name":   ind_name,
                    "raw_value":        raw,
                    "normalized_value": norm_val,
                    "direction":        direction,
                })
                ticker_raw[ind_name] = raw

            if indicator_rows:
                storage.write_indicator_outputs(indicator_rows)

            if ticker_raw:
                indicator_outputs[(ticker, exchange)] = ticker_raw

        logger.info(
            "Indicators computed for %d tickers (%d indicators each).",
            len(indicator_outputs), len(REGISTRY),
        )

        # ── Compute combo score + ranking ──────────────────────────────────
        ranked_df = score_tickers(
            indicator_outputs,
            effective_date,
            combination_name="default",
        )

        if not ranked_df.empty:
            storage.write_combo_results(ranked_df)
            logger.info("Combo results written: %d ranked rows.", len(ranked_df))

        # ── Finalise run log ───────────────────────────────────────────────
        status = "partial" if budget_exhausted else "completed"
        storage.log_run_end(run_id, status, budget.used)
        logger.info("Run %s finished with status='%s'.", run_id, status)

        # ── Verification output ────────────────────────────────────────────
        _emit_verification(ranked_df, output_path)

        return {
            "fetched":             n_fetched,
            "skipped_idempotent":  n_skipped_idempotent,
            "failed":              n_failed,
            "budget_exhausted":    budget_exhausted,
            "survivors":           len(survivors_df),
            "ranked":              len(ranked_df),
            "status":              status,
        }


def _emit_verification(ranked_df: pd.DataFrame, output_path: str | None) -> None:
    """Print top-10 ranked rows to stdout or write to CSV.

    This is a minimal Phase C verification dump — not the Phase D report.
    """
    if ranked_df.empty:
        print("[scanner] No ranked results to display.")
        return

    top = ranked_df.head(10)
    display_cols = [
        c for c in
        ["ticker", "exchange", "direction", "combo_score", "rank_score",
         "agreement_count", "vol_confirmation", "volume_confirmation"]
        if c in top.columns
    ]

    if output_path:
        ranked_df.to_csv(output_path, index=False)
        print(f"[scanner] Ranked results written to {output_path}  ({len(ranked_df)} rows)")
    else:
        print(f"\n[scanner] Top {len(top)} ranked tickers (of {len(ranked_df)} total):")
        print(top[display_cols].to_string(index=False))
        print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="scanner",
        description="Signal-scanner: deterministic global technical scanner.",
    )
    sub = parser.add_subparsers(dest="cmd", metavar="COMMAND")

    run_p = sub.add_parser(
        "run-daily",
        help="Run the daily scan for a universe scope.",
    )
    run_p.add_argument(
        "--universe",
        choices=["sample", "us", "global"],
        default="sample",
        help="Universe scope (default: sample).",
    )
    run_p.add_argument(
        "--output-path",
        default=None,
        metavar="PATH",
        help="Write ranked CSV to PATH instead of printing to stdout.",
    )

    args = parser.parse_args()

    if args.cmd == "run-daily":
        try:
            summary = run_daily(
                scope=args.universe,
                output_path=args.output_path,
            )
            print(
                f"[scanner] run-daily complete: "
                f"fetched={summary['fetched']}  "
                f"survivors={summary['survivors']}  "
                f"ranked={summary['ranked']}  "
                f"status={summary['status']}"
            )
            if summary["budget_exhausted"]:
                print("[scanner] WARNING: daily API budget exhausted mid-loop.")
            sys.exit(0)
        except Exception as exc:
            print(f"[scanner] run-daily failed: {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(0)
