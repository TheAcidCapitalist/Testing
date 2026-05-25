from scanner.data.storage import Storage
from scanner.cli import resample_ohlcv
from scanner.indicators import box_breakout

with Storage("data/scanner_test.duckdb") as storage:
    prices = storage.read_prices("AAPL", "US")

    # daily
    s = box_breakout.compute_series(prices, lookback=60, touch_tolerance=0.05, compression_threshold=1.5, duration_pct=0.5)
    print("Daily:", s["direction"].isin(["buy", "sell"]).sum())

    # weekly
    w_prices = resample_ohlcv(prices, "W-FRI")
    s = box_breakout.compute_series(w_prices, lookback=104, touch_tolerance=0.10, compression_threshold=1.5, duration_pct=0.5)
    print("Weekly:", s["direction"].isin(["buy", "sell"]).sum())

    # monthly
    m_prices = resample_ohlcv(prices, "ME")
    s = box_breakout.compute_series(m_prices, lookback=240, touch_tolerance=0.20, compression_threshold=1.5, duration_pct=0.5)
    print("Monthly:", s["direction"].isin(["buy", "sell"]).sum())
