from scanner.data.storage import Storage
from scanner.cli import BOX_BREAKOUT_MODES, resample_ohlcv
from scanner.indicators import box_breakout

with Storage("data/scanner_test.duckdb") as storage:
    tickers = storage._con.execute("SELECT DISTINCT ticker, exchange FROM tbl_prices").fetchall()
    
    counts = {"daily": 0, "weekly": 0, "monthly": 0}
    
    for t, e in tickers:
        prices = storage.read_prices(t, e)
        if prices.empty:
            continue
            
        for mode in BOX_BREAKOUT_MODES:
            res = mode["resolution"]
            if mode["freq"]:
                df_run = resample_ohlcv(prices, mode["freq"])
            else:
                df_run = prices
                
            if df_run.empty:
                continue
                
            series = box_breakout.compute_series(df_run, **mode["params"])
            detections = series["direction"].isin(["buy", "sell"]).sum()
            counts[res] += detections

    print("Historical Detections per mode:")
    for res, cnt in counts.items():
        print(f"  {res}: {cnt}")
