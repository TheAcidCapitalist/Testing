from scanner.data.storage import Storage
from scanner.cli import resample_ohlcv
from scanner.indicators import box_breakout

with Storage("data/scanner_test.duckdb") as storage:
    tickers = storage._con.execute("SELECT DISTINCT ticker, exchange FROM tbl_prices").fetchall()

    for comp in [1.5, 100.0]:
        counts = {"daily": 0, "weekly": 0, "monthly": 0}
        for t, e in tickers:
            prices = storage.read_prices(t, e)
            if prices.empty: continue
            
            # daily
            s = box_breakout.compute_series(prices, lookback=60, touch_tolerance=0.05, compression_threshold=comp, duration_pct=0.7)
            counts["daily"] += s["direction"].isin(["buy", "sell"]).sum()
            
            # weekly
            w = resample_ohlcv(prices, "W-FRI")
            if not w.empty:
                s = box_breakout.compute_series(w, lookback=104, touch_tolerance=0.15, compression_threshold=comp, duration_pct=0.7)
                counts["weekly"] += s["direction"].isin(["buy", "sell"]).sum()
                
            # monthly
            m = resample_ohlcv(prices, "ME")
            if not m.empty:
                s = box_breakout.compute_series(m, lookback=240, touch_tolerance=0.30, compression_threshold=comp, duration_pct=0.5)
                counts["monthly"] += s["direction"].isin(["buy", "sell"]).sum()

        print(f"Compression={comp}: {counts}")
