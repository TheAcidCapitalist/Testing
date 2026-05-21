"""YFinance metadata fetcher for universe metadata (Option C).

This module provides batch metadata fetching (market cap, sector, country)
for universe construction. It degrades gracefully: a failed ticker returns null
metadata and does not crash the batch.

It operates entirely independently of the EODHD pipeline and CallBudget.
"""

from __future__ import annotations

import logging

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

def fetch_yfinance_meta(tickers: list[str]) -> pd.DataFrame:
    """Fetch market cap, sector, and country metadata for a list of tickers.

    Fails gracefully on individual ticker errors (returns null metadata for them)
    and does not crash the batch.

    Parameters
    ----------
    tickers:
        List of tickers (yfinance format, e.g. 'AAPL', 'VOD.L').

    Returns
    -------
    pd.DataFrame
        Keyed by 'ticker', with columns 'market_cap', 'sector', 'country'.
        Rows are in the same order as the input tickers.
    """
    results = []

    if not tickers:
        return pd.DataFrame(columns=["ticker", "market_cap", "sector", "country"])

    for symbol in tickers:
        try:
            t = yf.Ticker(symbol)
            info = t.info
            
            if not isinstance(info, dict):
                info = {}

            results.append({
                "ticker": symbol,
                "market_cap": info.get("marketCap"),
                "sector": info.get("sector"),
                "country": info.get("country"),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] yfinance metadata fetch failed: %s", symbol, exc)
            results.append({
                "ticker": symbol,
                "market_cap": None,
                "sector": None,
                "country": None,
            })

    return pd.DataFrame(results)
