"""Tests for MTF resampling and resolution storage."""

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from scanner.cli import resample_ohlcv
from scanner.data.storage import Storage

def test_resample_ohlcv_weekly():
    dates = pd.date_range("2020-01-01", periods=10, freq="B")  # Starts on Wed
    # Wed, Thu, Fri, Mon, Tue, Wed, Thu, Fri, Mon, Tue
    df = pd.DataFrame({
        "date": dates,
        "open": range(10),
        "high": range(10, 20),
        "low": range(-10, 0),
        "close": range(20, 30),
        "adj_close": range(20, 30),
        "volume": [100] * 10,
        "source": "test"
    })
    
    # 2020-01-01 is Wed. Week ends on 2020-01-03 (Fri).
    # Last date is 2020-01-14 (Tue). The week for this ends on 2020-01-17 (Fri).
    # Since 2020-01-14 < 2020-01-17, the last week should be dropped!
    
    res = resample_ohlcv(df, "W-FRI")
    
    # Should only have complete weeks.
    # Week 1: Wed-Fri (3 days). complete.
    # Week 2: Mon-Fri (5 days). complete.
    # Week 3: Mon-Tue (2 days). incomplete, dropped.
    assert len(res) == 2
    
    # Check week 1 aggregation
    w1 = res.iloc[0]
    assert w1["date"] == pd.Timestamp("2020-01-03")
    assert w1["open"] == 0   # first open
    assert w1["high"] == 12  # max high
    assert w1["low"] == -10  # min low
    assert w1["close"] == 22 # last close
    assert w1["volume"] == 300 # sum volume
    
    # Check week 2 aggregation
    w2 = res.iloc[1]
    assert w2["date"] == pd.Timestamp("2020-01-10")
    assert w2["open"] == 3
    assert w2["high"] == 17
    assert w2["low"] == -7
    assert w2["close"] == 27
    assert w2["volume"] == 500

def test_storage_resolution_key(tmp_path):
    db_path = tmp_path / "test.db"
    
    with Storage(db_path) as storage:
        rows = [
            {
                "ticker": "AAPL",
                "exchange": "US",
                "date": date(2020, 1, 1),
                "indicator_name": "box_breakout",
                "resolution": "daily",
                "raw_value": {"direction": "buy"},
                "normalized_value": 1.0,
                "direction": "buy"
            },
            {
                "ticker": "AAPL",
                "exchange": "US",
                "date": date(2020, 1, 1), # Same date and indicator, different resolution
                "indicator_name": "box_breakout",
                "resolution": "weekly",
                "raw_value": {"direction": "sell"},
                "normalized_value": 0.0,
                "direction": "sell"
            }
        ]
        
        storage.write_indicator_outputs(rows)
        
        daily = storage.read_indicator_outputs("AAPL", "US", date(2020, 1, 1), resolution="daily")
        weekly = storage.read_indicator_outputs("AAPL", "US", date(2020, 1, 1), resolution="weekly")
        
        assert daily["box_breakout"]["direction"] == "buy"
        assert weekly["box_breakout"]["direction"] == "sell"
