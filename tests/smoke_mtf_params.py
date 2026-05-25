from scanner.data.storage import Storage
from scanner.cli import BOX_BREAKOUT_MODES, resample_ohlcv
from scanner.indicators import box_breakout

with Storage("data/scanner_test.duckdb") as storage:
    prices = storage.read_prices("AAPL", "US")

    for duration in [0.5, 0.6, 0.75]:
        for compression in [0.8, 1.0, 1.5, 2.0]:
            series = box_breakout.compute_series(prices, lookback=60, touch_tolerance=0.02, 
                                                compression_threshold=compression, duration_pct=duration)
            detections = series["direction"].isin(["buy", "sell"]).sum()
            print(f"duration={duration}, compression={compression} -> {detections} detections")

