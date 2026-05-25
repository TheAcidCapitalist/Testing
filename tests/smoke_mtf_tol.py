from scanner.data.storage import Storage
from scanner.cli import BOX_BREAKOUT_MODES, resample_ohlcv
from scanner.indicators import box_breakout

with Storage("data/scanner_test.duckdb") as storage:
    prices = storage.read_prices("AAPL", "US")

    for tol in [0.02, 0.05, 0.10, 0.15, 0.20]:
        series = box_breakout.compute_series(prices, lookback=60, touch_tolerance=tol, 
                                            compression_threshold=2.0, duration_pct=0.5)
        detections = series["direction"].isin(["buy", "sell"]).sum()
        print(f"tol={tol} -> {detections} detections")

