from scanner.data.storage import Storage
from scanner.cli import resample_ohlcv
from scanner.indicators import box_breakout

with Storage("data/scanner_test.duckdb") as storage:
    prices = storage.read_prices("AAPL", "US")
    w_prices = resample_ohlcv(prices, "W-FRI")
    m_prices = resample_ohlcv(prices, "ME")

    print("Weekly:")
    for tol in [0.05, 0.1, 0.15, 0.2, 0.25]:
        s = box_breakout.compute_series(w_prices, lookback=104, touch_tolerance=tol, compression_threshold=1.5, duration_pct=0.5)
        print(f"tol={tol} -> {s['direction'].isin(['buy', 'sell']).sum()}")

    print("Monthly:")
    for tol in [0.1, 0.2, 0.3, 0.4, 0.5]:
        s = box_breakout.compute_series(m_prices, lookback=240, touch_tolerance=tol, compression_threshold=1.5, duration_pct=0.5)
        print(f"tol={tol} -> {s['direction'].isin(['buy', 'sell']).sum()}")
