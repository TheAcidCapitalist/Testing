"""Scoring engine — weighted combo formula and tiered ranking."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# Load weights from spec/scoring.md at import time
# ---------------------------------------------------------------------------

_SPEC_PATH = Path(__file__).parents[3] / "spec" / "scoring.md"


def _load_weights() -> dict[str, float]:
    """Parse the [weights] TOML block embedded in spec/scoring.md."""
    text = _SPEC_PATH.read_text()
    match = re.search(r"```toml\s*\[weights\](.*?)```", text, re.DOTALL)
    if not match:
        raise ValueError("Could not find [weights] TOML block in spec/scoring.md")
    toml_str = "[weights]\n" + match.group(1).strip()
    data = tomllib.loads(toml_str)
    return data["weights"]  # type: ignore[return-value]


WEIGHTS: dict[str, float] = _load_weights()


# ---------------------------------------------------------------------------
# Score columns expected from each indicator
# ---------------------------------------------------------------------------

_SCORE_COLS = {
    "rsi": "rsi_score",
    "stochastic": "stochastic_score",
    "mav_breakout": "mav_breakout_score",
    "daily_trend": "daily_trend_score",
    "bollinger": "bollinger_score",
    "volatility": "volatility_score",
    "volume": "volume_score",
    "mav_diff_z": "mav_diff_z_score",
}


def compute_combo_score(df: pd.DataFrame) -> pd.DataFrame:
    """Add ``combo_score`` and ``tier`` columns to *df* (last-row signals).

    *df* must already have all indicator score columns present.
    """
    score = pd.Series(0.0, index=df.index)
    for ind_name, col in _SCORE_COLS.items():
        if col not in df.columns:
            continue
        w = WEIGHTS.get(ind_name, 0.0)
        score += w * df[col].fillna(0.0)

    df["combo_score"] = score
    df["tier"] = pd.cut(
        score,
        bins=[-1, 0.5, 0.7, 2.0],
        labels=["weak", "moderate", "strong"],
    )
    return df


def rank_results(df: pd.DataFrame) -> pd.DataFrame:
    """Apply ranking rules from spec/scoring.md and return sorted DataFrame.

    *df* should be a one-row-per-ticker DataFrame with the latest indicator
    values + combo_score.
    """
    # Exclude falling knives in strong downtrends
    mask_exclude = (df.get("daily_trend_signal", "") == "down") & (df["combo_score"] < 0.4)
    df = df[~mask_exclude].copy()

    df = df.sort_values(
        ["combo_score", "rsi_value"],
        ascending=[False, True],  # higher score first; lower RSI (more oversold) first on ties
    ).reset_index(drop=True)

    return df
