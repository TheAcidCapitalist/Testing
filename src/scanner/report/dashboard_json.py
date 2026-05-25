"""Dashboard JSON emitter — canonical output for all Phase D consumers.

Reads from Storage (combo results, indicator outputs, universe metadata)
and produces a dict / JSON file.  No scoring, no fetching, no alignment
re-derivation.

See ``spec/dashboard-json.md`` for the full contract.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

from scanner.data.storage import Storage

_SCHEMA_VERSION = "1.0"

# Resolutions to fetch for Box Breakout MTF indicator detail.
_MTF_RESOLUTIONS = ("weekly", "monthly")


def build_dashboard_dict(
    storage: Storage,
    run_date: date,
    scope: str,
    combination_name: str = "default",
    n_tickers_universe: int = 0,
) -> dict:
    """Build the canonical dashboard JSON dict from storage.

    Parameters
    ----------
    storage:
        Open ``Storage`` instance to read from.
    run_date:
        The date of the run.
    scope:
        Universe scope (``"sample"``, ``"us"``, ``"global"``).
    combination_name:
        Combination key (default ``"default"``).
    n_tickers_universe:
        Pre-filter universe size (for the envelope).

    Returns
    -------
    dict
        The full dashboard dict ready for ``json.dumps()`` or
        ``write_dashboard_json()``.
    """
    # ── Read all combo results for this date + combination ──────────────────
    combo_df = storage.read_combo_results_all(run_date, combination_name)

    if combo_df.empty:
        return {
            "meta": _build_meta(
                run_date=run_date,
                scope=scope,
                combination_name=combination_name,
                n_tickers_scored=0,
                n_tickers_universe=n_tickers_universe,
                n_buy=0,
                n_sell=0,
                n_neutral=0,
            ),
            "tickers": [],
        }

    # Sort by rank_score descending — the contract guarantees this order.
    combo_df = combo_df.sort_values("rank_score", ascending=False).reset_index(
        drop=True
    )

    # ── Load universe metadata for joining ──────────────────────────────────
    universe_df = storage.read_universe()
    universe_map: dict[tuple[str, str], dict] = {}
    for _, row in universe_df.iterrows():
        universe_map[(row["ticker"], row["exchange"])] = {
            "name": row.get("name"),
            "currency": row.get("currency"),
            "market_cap_usd": _safe_float(row.get("market_cap_usd")),
            "sector": row.get("sector"),
            "region": row.get("region"),
        }

    # ── Build per-ticker entries ────────────────────────────────────────────
    tickers_out: list[dict] = []
    n_buy = n_sell = n_neutral = 0

    for _, combo_row in combo_df.iterrows():
        ticker = combo_row["ticker"]
        exchange = combo_row["exchange"]
        direction = combo_row["direction"] or "neutral"

        # Direction counts
        if direction == "buy":
            n_buy += 1
        elif direction == "sell":
            n_sell += 1
        else:
            n_neutral += 1

        # Parse signals_firing from JSON string
        sf_raw = combo_row["signals_firing"]
        if isinstance(sf_raw, str):
            signals_firing = json.loads(sf_raw)
        elif isinstance(sf_raw, list):
            signals_firing = sf_raw
        else:
            signals_firing = []

        # Core fields
        entry: dict = {
            "ticker": ticker,
            "exchange": exchange,
            "date": _date_str(combo_row["date"]),
            "direction": direction,
            "combo_score": float(combo_row["combo_score"]),
            "rank_score": float(combo_row["rank_score"]),
            "agreement_count": int(combo_row["agreement_count"]),
            "n_trade_indicators": int(combo_row["n_trade_indicators"]),
            "signals_firing": signals_firing,
            "vol_confirmation": combo_row["vol_confirmation"],
            "volume_confirmation": combo_row["volume_confirmation"],
            "days_since_breakout": _safe_int(combo_row["days_since_breakout"]),
        }

        # Universe metadata — null-tolerant
        entry["meta"] = universe_map.get(
            (ticker, exchange),
            {
                "name": None,
                "currency": None,
                "market_cap_usd": None,
                "sector": None,
                "region": None,
            },
        )

        # MTF alignment — read persisted values, never recompute
        entry["mtf_alignment"] = {
            "resolutions_available": int(combo_row.get("resolutions_available", 0)),
            "resolutions_aligned": int(combo_row.get("resolutions_aligned", 0)),
            "alignment_fraction": float(combo_row.get("alignment_fraction", 0.0)),
        }

        # Indicator detail — tiered by direction
        indicators = _build_indicator_detail(
            storage=storage,
            ticker=ticker,
            exchange=exchange,
            run_date=run_date,
            direction=direction,
        )
        entry["indicators"] = indicators

        tickers_out.append(entry)

    return {
        "meta": _build_meta(
            run_date=run_date,
            scope=scope,
            combination_name=combination_name,
            n_tickers_scored=len(tickers_out),
            n_tickers_universe=n_tickers_universe,
            n_buy=n_buy,
            n_sell=n_sell,
            n_neutral=n_neutral,
        ),
        "tickers": tickers_out,
    }


def write_dashboard_json(data: dict, path: str | Path) -> Path:
    """Write a dict (from ``build_dashboard_dict``) to a JSON file.

    Creates parent directories if needed.  Returns the resolved ``Path``.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f:
        json.dump(data, f, indent=2, default=str)
    return p


# ── Private helpers ──────────────────────────────────────────────────────────


def _build_meta(
    *,
    run_date: date,
    scope: str,
    combination_name: str,
    n_tickers_scored: int,
    n_tickers_universe: int,
    n_buy: int,
    n_sell: int,
    n_neutral: int,
) -> dict:
    return {
        "schema_version": _SCHEMA_VERSION,
        "run_date": _date_str(run_date),
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": scope,
        "combination_name": combination_name,
        "n_tickers_scored": n_tickers_scored,
        "n_tickers_universe": n_tickers_universe,
        "n_buy": n_buy,
        "n_sell": n_sell,
        "n_neutral": n_neutral,
    }


def _build_indicator_detail(
    *,
    storage: Storage,
    ticker: str,
    exchange: str,
    run_date: date,
    direction: str,
) -> dict:
    """Build the per-indicator detail dict for one ticker.

    Non-neutral: full raw_value + normalized_value + direction.
    Neutral: summary only (direction + normalized_value).
    """
    full = direction != "neutral"

    # Daily indicators
    daily = storage.read_indicator_outputs(ticker, exchange, run_date, "daily")
    indicators: dict = {}

    for name, data in daily.items():
        if full:
            indicators[name] = {
                "raw_value": data["raw_value"],
                "normalized_value": data["normalized_value"],
                "direction": data["direction"],
            }
        else:
            indicators[name] = {
                "normalized_value": data["normalized_value"],
                "direction": data["direction"],
            }

    # MTF resolutions — flat keys (box_breakout_weekly, box_breakout_monthly)
    for res in _MTF_RESOLUTIONS:
        mtf_data = storage.read_indicator_outputs(ticker, exchange, run_date, res)
        for name, data in mtf_data.items():
            flat_key = f"{name}_{res}"
            if full:
                indicators[flat_key] = {
                    "raw_value": data["raw_value"],
                    "normalized_value": data["normalized_value"],
                    "direction": data["direction"],
                }
            else:
                indicators[flat_key] = {
                    "normalized_value": data["normalized_value"],
                    "direction": data["direction"],
                }

    return indicators


def _date_str(d: date | str) -> str:
    """Convert a date (or date-like) to 'YYYY-MM-DD' string."""
    if isinstance(d, str):
        return d[:10]  # truncate any time component
    if isinstance(d, datetime):
        return d.date().isoformat()
    return d.isoformat()


def _safe_int(v: object) -> int | None:
    """Convert to int, returning None for None / NaN / pd.NA."""
    if v is None:
        return None
    try:
        import pandas as pd
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return int(v)


def _safe_float(v: object) -> float | None:
    """Convert to float, returning None for None / NaN / pd.NA."""
    if v is None:
        return None
    try:
        import pandas as pd
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
