from scanner.data.storage import Storage
from scanner.cli import resample_ohlcv, BOX_BREAKOUT_MODES
from scanner.indicators import box_breakout

with Storage("data/scanner_test.duckdb") as storage:
    tickers = storage._con.execute("SELECT DISTINCT ticker, exchange FROM tbl_prices").fetchall()

    counts = {"daily": 0, "weekly": 0, "monthly": 0}
    for t, e in tickers:
        prices = storage.read_prices(t, e)
        if prices.empty: continue
        
        for mode in BOX_BREAKOUT_MODES:
            res = mode["resolution"]
            df_run = resample_ohlcv(prices, mode["freq"]) if mode["freq"] else prices
            if df_run.empty: continue
            
            s = box_breakout.compute_series(df_run, **mode["params"])
            counts[res] += s["direction"].isin(["buy", "sell"]).sum()

    print("Post-Gate Final Detections:")
    print(counts)
