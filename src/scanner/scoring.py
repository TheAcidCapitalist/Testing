"""Combo scoring and ranking for signal-scanner.

Implements the two-stage pipeline from spec/scoring.md:

  Stage 1 — normalize each indicator's compute() result to [0, 1]
             (low = buy, high = sell, matching the source combo convention)
  Stage 2 — weighted mean over the selected combination's indicators
             (combo_score), then rank by agreement, magnitude, confirmation,
             and breakout recency (rank_score).

The default combination is all 8 trade indicators + Volatility + Volume.
MAV Diff Z-Score is a backtest exit signal only — it is NOT included in any
combo score and is never passed to normalize() by the orchestrator.

Combinations
------------
A combination is a named, weighted selection of indicators.  The three seeded
combinations below are hardcoded as Python dicts; Phase D / Phase E may move
them to a config file or DuckDB table without touching this module.

Usage
-----
    from scanner.scoring import normalize, score_tickers, COMBINATIONS

    # Normalize a single indicator result:
    norm = normalize("rsi", {"rsi": 42.5, ...})   # → 0.425

    # Score and rank a set of tickers:
    ranked_df = score_tickers(
        indicator_outputs,   # dict[(ticker, exchange)] → {ind_name: raw_dict}
        run_date=date.today(),
        combination_name="default",
    )
"""

from __future__ import annotations

from datetime import date

import pandas as pd

# ---------------------------------------------------------------------------
# Combination registry
# ---------------------------------------------------------------------------

#: Trade indicators — contribute to agreement_count.
TRADE_INDICATORS: frozenset[str] = frozenset({
    "rsi",
    "daily_trend_divergence",
    "daily_trend_contrarian",
    "bollinger_normal",
    "bollinger_contrarian",
    "mav_breakout",
    "box_breakout",
    "stochastic",
})

#: Seeded combinations.  Equal weights reproduce the original spreadsheet's
#: arithmetic mean.  Phase E will tune these based on backtest results.
COMBINATIONS: dict[str, dict] = {
    "default": {
        "name": "default",
        "indicators": [
            {"indicator": "rsi",                    "weight": 1.0},
            {"indicator": "daily_trend_divergence", "weight": 1.0},
            {"indicator": "daily_trend_contrarian", "weight": 1.0},
            {"indicator": "bollinger_normal",       "weight": 1.0},
            {"indicator": "bollinger_contrarian",   "weight": 1.0},
            {"indicator": "mav_breakout",           "weight": 1.0},
            {"indicator": "box_breakout",           "weight": 1.0},
            {"indicator": "stochastic",             "weight": 1.0},
            {"indicator": "volatility",             "weight": 1.0},
            {"indicator": "volume",                 "weight": 1.0},
        ],
    },
    "breakout_family": {
        "name": "breakout_family",
        "indicators": [
            {"indicator": "mav_breakout",     "weight": 1.0},
            {"indicator": "bollinger_normal", "weight": 1.0},
            {"indicator": "box_breakout",     "weight": 1.0},
        ],
    },
    "mean_reversion": {
        "name": "mean_reversion",
        "indicators": [
            {"indicator": "rsi",                  "weight": 1.0},
            {"indicator": "bollinger_contrarian", "weight": 1.0},
            {"indicator": "stochastic",           "weight": 1.0},
        ],
    },
}

# Rank-score weights (v1 — equal-ish start; backtest decides real values).
_W_AGREE = 0.40
_W_MAGNITUDE = 0.30
_W_CONFIRM = 0.20
_W_STALENESS = 0.10
_W_MTF = 0.0  # multi-timeframe: not active in v1 daily-only runs


# ---------------------------------------------------------------------------
# Stage 1 — normalization
# ---------------------------------------------------------------------------


def normalize(indicator_name: str, raw_value: dict) -> float | None:
    """Map an indicator's compute() dict to a [0, 1] normalized score.

    Returns ``None`` for indicators excluded from combo scoring (mav_diff_z).
    Returns 0.5 (neutral) for any unrecognised indicator name.

    Convention: **low score = buy signal, high score = sell signal.**

    Normalization per indicator (from spec/scoring.md Stage 1):

    * ``rsi``                    — rsi / 100
    * ``daily_trend_divergence`` — direction-based: buy→0.25, sell→0.75, neutral→0.5
    * ``daily_trend_contrarian`` — same direction-based mapping (contrarian
                                   direction is already inverted in the indicator)
    * ``bollinger_normal``       — direction-based: buy→0.25, sell→0.75, neutral→0.5
                                   (signal_value is the raw z-score, not the combo score)
    * ``bollinger_contrarian``   — same direction-based mapping
    * ``mav_breakout``           — signal_value (0.25 / 0.5 / 0.75)
    * ``box_breakout``           — signal_value (0.25 / 0.5 / 0.75)
    * ``stochastic``             — signal_value (K/100 when divergence fires, 0.5 neutral)
    * ``volatility``             — percentile directly (low vol pct = confirm = low score)
    * ``volume``                 — 1 − percentile (high vol pct = confirm = low score)
    * ``mav_diff_z``             — None (backtest exit only; not in combo)
    """
    if indicator_name == "mav_diff_z":
        return None

    if indicator_name == "rsi":
        return float(raw_value["rsi"]) / 100.0

    # These indicators expose signal_value as the correct 0-1 normalized score.
    # mav_breakout / box_breakout: 0.25 buy | 0.75 sell | 0.50 neutral.
    # stochastic: K/100 when divergence fires (K<20 → ≈0.1 = low = buy;
    #             K>80 → ≈0.9 = high = sell), 0.5 when no signal.
    if indicator_name in ("mav_breakout", "box_breakout", "stochastic"):
        return float(raw_value["signal_value"])

    # Bollinger signal_value is the raw z-score, NOT the three-value score.
    # Use direction-based mapping per spec/scoring.md "three-value scheme":
    # 0.25 if buy breakout, 0.75 if sell breakout, 0.5 if neutral.
    if indicator_name in ("bollinger_normal", "bollinger_contrarian",
                          "daily_trend_divergence", "daily_trend_contrarian"):
        direction = raw_value.get("direction", "neutral")
        if direction == "buy":
            return 0.25
        if direction == "sell":
            return 0.75
        return 0.5

    if indicator_name == "volatility":
        return float(raw_value["percentile"])

    if indicator_name == "volume":
        return 1.0 - float(raw_value["percentile"])

    # Unknown indicator: treat as neutral
    return 0.5


# ---------------------------------------------------------------------------
# Stage 2 — combo score + ranking
# ---------------------------------------------------------------------------


def score_tickers(
    indicator_outputs: dict[tuple[str, str], dict[str, dict]],
    run_date: date,
    *,
    combination_name: str = "default",
) -> pd.DataFrame:
    """Compute combo_score and rank_score for each ticker.

    Parameters
    ----------
    indicator_outputs:
        ``{(ticker, exchange): {indicator_name: raw_compute_dict}}``.
        Each inner dict is the return value of the indicator's ``compute()``
        function.
    run_date:
        The date of the run — stamped on every output row.
    combination_name:
        Key in :data:`COMBINATIONS` to use.  Default is ``"default"``.

    Returns
    -------
    DataFrame with columns matching ``tbl_combo_results``::

        ticker, exchange, date, combination_name,
        direction, combo_score, rank_score,
        agreement_count, n_trade_indicators,
        signals_firing (list),
        vol_confirmation, volume_confirmation,
        days_since_breakout

    Sorted descending by ``rank_score``.  Empty DataFrame if no tickers
    produce a valid score (e.g. no indicator data at all).
    """
    combo = COMBINATIONS[combination_name]
    combo_map: dict[str, float] = {
        e["indicator"]: e["weight"] for e in combo["indicators"]
    }
    trade_in_combo = [ind for ind in combo_map if ind in TRADE_INDICATORS]

    rows: list[dict] = []
    for (ticker, exchange), outputs in indicator_outputs.items():
        row = _score_one(
            ticker=ticker,
            exchange=exchange,
            outputs=outputs,
            run_date=run_date,
            combo_map=combo_map,
            trade_in_combo=trade_in_combo,
            combination_name=combination_name,
        )
        if row is not None:
            rows.append(row)

    if not rows:
        return pd.DataFrame(columns=[
            "ticker", "exchange", "date", "combination_name",
            "direction", "combo_score", "rank_score",
            "agreement_count", "n_trade_indicators",
            "signals_firing", "vol_confirmation", "volume_confirmation",
            "days_since_breakout",
        ])

    df = pd.DataFrame(rows)
    return df.sort_values("rank_score", ascending=False).reset_index(drop=True)


def _score_one(
    *,
    ticker: str,
    exchange: str,
    outputs: dict[str, dict],
    run_date: date,
    combo_map: dict[str, float],
    trade_in_combo: list[str],
    combination_name: str,
) -> dict | None:
    """Score a single ticker.  Returns None if no combo members have data."""
    total_weight = 0.0
    weighted_sum = 0.0

    for ind_name, weight in combo_map.items():
        if ind_name not in outputs:
            continue
        norm = normalize(ind_name, outputs[ind_name])
        if norm is None:
            continue
        weighted_sum += weight * norm
        total_weight += weight

    if total_weight == 0.0:
        return None

    combo_score = weighted_sum / total_weight

    # ── Direction ──────────────────────────────────────────────────────────
    if combo_score < 0.3:
        direction = "buy"
    elif combo_score > 0.7:
        direction = "sell"
    else:
        direction = "neutral"

    # ── Agreement count (trade indicators only) ────────────────────────────
    n_trade = len(trade_in_combo)
    if direction == "neutral":
        agreement_count = 0
        signals_firing: list[str] = []
    else:
        signals_firing = [
            ind for ind in trade_in_combo
            if ind in outputs and outputs[ind].get("direction") == direction
        ]
        agreement_count = len(signals_firing)

    # ── Confirmation states (always from storage, regardless of combo) ─────
    vol_state = outputs.get("volatility", {}).get("state", "neutral")
    volume_state = outputs.get("volume", {}).get("state", "neutral")

    # Confirmation multiplier: each "reject" state demotes to 0.6.
    confirmation_mult = 1.0
    if vol_state == "reject":
        confirmation_mult *= 0.6
    if volume_state == "reject":
        confirmation_mult *= 0.6

    # ── Breakout recency ───────────────────────────────────────────────────
    # "days since signal" for each breakout-type indicator, when active.
    dsb_list: list[int] = []

    # mav_breakout: abs(breakout_flag) is days since the signal fired.
    # breakout_flag == 0 means no signal has ever fired.
    if "mav_breakout" in outputs:
        bf = outputs["mav_breakout"].get("breakout_flag", 0)
        if bf != 0:
            dsb_list.append(abs(int(bf)))

    # box_breakout: days_since_breakout is directly the days since signal.
    if "box_breakout" in outputs:
        dsb = outputs["box_breakout"].get("days_since_breakout")
        if dsb is not None:
            dsb_list.append(int(dsb))

    # bollinger_normal/contrarian: bollinger_days = consecutive bars in band
    for ind in ("bollinger_normal", "bollinger_contrarian"):
        if ind in outputs:
            b_days = outputs[ind].get("bollinger_days")
            if b_days is not None and outputs[ind].get("direction", "neutral") != "neutral":
                dsb_list.append(int(b_days))

    days_since_breakout: int | None = min(dsb_list) if dsb_list else None

    # ── Rank score ─────────────────────────────────────────────────────────
    agree_term = (agreement_count / n_trade) if n_trade > 0 else 0.0
    magnitude_term = abs(combo_score - 0.5) * 2.0
    staleness_term = (
        min(days_since_breakout, 20) / 20.0 if days_since_breakout is not None else 0.0
    )

    rank_score = (
        _W_AGREE * agree_term
        + _W_MAGNITUDE * magnitude_term
        + _W_CONFIRM * confirmation_mult
        - _W_STALENESS * staleness_term
        # _W_MTF * 0.0 — omitted (v1 daily-only)
    )

    return {
        "ticker":               ticker,
        "exchange":             exchange,
        "date":                 run_date,
        "combination_name":     combination_name,
        "direction":            direction,
        "combo_score":          combo_score,
        "rank_score":           rank_score,
        "agreement_count":      agreement_count,
        "n_trade_indicators":   n_trade,
        "signals_firing":       signals_firing,
        "vol_confirmation":     vol_state,
        "volume_confirmation":  volume_state,
        "days_since_breakout":  days_since_breakout,
    }
