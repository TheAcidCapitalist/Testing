"""Tests for src/scanner/data/yfinance_meta.py.

All tests mock yfinance — no real network calls are made.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd

from scanner.data.yfinance_meta import fetch_yfinance_meta


def test_fetch_empty_list_returns_empty_dataframe():
    df = fetch_yfinance_meta([])
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 0
    assert list(df.columns) == ["ticker", "market_cap", "sector", "country"]


def test_happy_path_returns_expected_columns_and_data():
    mock_ticker_aapl = MagicMock()
    mock_ticker_aapl.info = {"marketCap": 3_000_000_000, "sector": "Technology", "country": "USA"}
    
    mock_ticker_msft = MagicMock()
    mock_ticker_msft.info = {"marketCap": 2_500_000_000, "sector": "Technology", "country": "USA"}

    def mock_ticker(symbol):
        if symbol == "AAPL":
            return mock_ticker_aapl
        return mock_ticker_msft

    with patch("scanner.data.yfinance_meta.yf.Ticker", side_effect=mock_ticker):
        df = fetch_yfinance_meta(["AAPL", "MSFT"])

    assert len(df) == 2
    assert list(df.columns) == ["ticker", "market_cap", "sector", "country"]
    
    row0 = df.iloc[0]
    assert row0["ticker"] == "AAPL"
    assert row0["market_cap"] == 3_000_000_000
    assert row0["sector"] == "Technology"
    assert row0["country"] == "USA"

    row1 = df.iloc[1]
    assert row1["ticker"] == "MSFT"
    assert row1["market_cap"] == 2_500_000_000


def test_ticker_exception_degrades_gracefully():
    mock_ticker_aapl = MagicMock()
    mock_ticker_aapl.info = {"marketCap": 3_000_000_000, "sector": "Technology", "country": "USA"}

    def mock_ticker(symbol):
        if symbol == "AAPL":
            return mock_ticker_aapl
        raise ValueError("yfinance network error")

    with patch("scanner.data.yfinance_meta.yf.Ticker", side_effect=mock_ticker):
        df = fetch_yfinance_meta(["AAPL", "FAIL_TICKER"])

    assert len(df) == 2
    
    row1 = df.iloc[1]
    assert row1["ticker"] == "FAIL_TICKER"
    assert pd.isna(row1["market_cap"]) or row1["market_cap"] is None
    assert row1["sector"] is None
    assert row1["country"] is None


def test_garbage_response_handled_without_crashing():
    mock_ticker_garbage = MagicMock()
    # Simulate yfinance returning a weird non-dict structure or a dict missing fields
    mock_ticker_garbage.info = {"trailingPegRatio": None}
    
    def mock_ticker(symbol):
        return mock_ticker_garbage

    with patch("scanner.data.yfinance_meta.yf.Ticker", side_effect=mock_ticker):
        df = fetch_yfinance_meta(["GARBAGE"])

    assert len(df) == 1
    row = df.iloc[0]
    assert row["ticker"] == "GARBAGE"
    assert row["market_cap"] is None
    assert row["sector"] is None
    assert row["country"] is None


def test_info_is_not_a_dict():
    mock_ticker_weird = MagicMock()
    mock_ticker_weird.info = None
    
    with patch("scanner.data.yfinance_meta.yf.Ticker", return_value=mock_ticker_weird):
        df = fetch_yfinance_meta(["WEIRD"])

    assert len(df) == 1
    row = df.iloc[0]
    assert row["ticker"] == "WEIRD"
    assert row["market_cap"] is None
    assert row["sector"] is None
