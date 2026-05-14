"""Dashboard JSON exporter — writes data/latest.json for the HTML artifact."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd


def export_json(results: pd.DataFrame, scan_date: date, output_path: Path) -> Path:
    """Serialise scan results to JSON for the dashboard artifact.

    The output schema is:
    {
      "scan_date": "YYYY-MM-DD",
      "generated_at": "<ISO timestamp>",
      "tickers": [
        {
          "rank": 1,
          "ticker": "AAPL",
          "exchange": "US",
          "combo_score": 0.82,
          "tier": "strong",
          "rsi_value": 28.4,
          ...
        },
        ...
      ]
    }
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    records = results.head(100).copy()
    records.insert(0, "rank", range(1, len(records) + 1))

    # Convert non-serialisable types
    for col in records.select_dtypes(include=["datetime", "datetimetz"]).columns:
        records[col] = records[col].dt.strftime("%Y-%m-%d")

    payload = {
        "scan_date": scan_date.isoformat(),
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "tickers": records.where(pd.notna(records), None).to_dict("records"),
    }

    output_path.write_text(json.dumps(payload, indent=2, default=str))
    return output_path
