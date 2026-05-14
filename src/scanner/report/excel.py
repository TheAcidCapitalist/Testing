"""Excel report builder."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils.dataframe import dataframe_to_rows

_TIER_FILLS = {
    "strong": PatternFill("solid", fgColor="70AD47"),    # green
    "moderate": PatternFill("solid", fgColor="FFC000"),  # amber
    "weak": PatternFill("solid", fgColor="FF4040"),      # red
}

_HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
_HEADER_FONT = Font(color="FFFFFF", bold=True)


def build_excel(results: pd.DataFrame, scan_date: date, output_path: Path) -> Path:
    """Write a colour-coded Excel workbook.

    Parameters
    ----------
    results:
        Ranked DataFrame — one row per ticker, all indicator columns included.
    scan_date:
        The date the scan was run.
    output_path:
        Where to write the .xlsx file.

    Returns
    -------
    The resolved output path.
    """
    wb = Workbook()

    # ── Summary sheet ──────────────────────────────────────────────────────────
    ws_summary = wb.active
    ws_summary.title = "Summary"
    _write_sheet(ws_summary, results, tier_col="tier")

    # ── Per-indicator sheets ───────────────────────────────────────────────────
    indicator_cols = {
        "RSI": ["ticker", "rsi_value", "rsi_signal", "rsi_score"],
        "Stochastic": ["ticker", "stoch_k", "stoch_d", "stochastic_signal", "stochastic_score"],
        "MAV Breakout": ["ticker", "mav_breakout_signal", "mav_breakout_score"],
        "Daily Trend": ["ticker", "daily_trend_slope", "daily_trend_signal", "daily_trend_score"],
        "Bollinger": ["ticker", "bb_pct_b", "bollinger_signal", "bollinger_score"],
        "Volatility": ["ticker", "vol_ann", "vol_z", "volatility_signal", "volatility_score"],
        "Volume": ["ticker", "volume_ratio", "volume_signal", "volume_score"],
        "MAV Diff Z": ["ticker", "mav_diff_z_value", "mav_diff_z_signal", "mav_diff_z_score"],
    }

    for sheet_name, cols in indicator_cols.items():
        available = [c for c in cols if c in results.columns]
        if not available:
            continue
        ws = wb.create_sheet(sheet_name)
        _write_sheet(ws, results[available])

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)
    return output_path


def _write_sheet(ws, df: pd.DataFrame, tier_col: str | None = None) -> None:
    """Write *df* to *ws* with header formatting."""
    for r_idx, row in enumerate(dataframe_to_rows(df, index=False, header=True), start=1):
        ws.append(row)
        for cell in ws[r_idx]:
            cell.alignment = Alignment(horizontal="center")
            if r_idx == 1:
                cell.fill = _HEADER_FILL
                cell.font = _HEADER_FONT
            elif tier_col is not None:
                tier_val = df.iloc[r_idx - 2].get(tier_col, None) if r_idx > 1 else None
                if tier_val and tier_val in _TIER_FILLS:
                    cell.fill = _TIER_FILLS[tier_val]

    # Auto-size columns
    for col in ws.columns:
        max_len = max((len(str(cell.value or "")) for cell in col), default=10)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 40)
