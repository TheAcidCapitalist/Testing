from scanner.data.storage import Storage
from scanner.cli import resample_ohlcv
from scanner.indicators import box_breakout

with Storage("data/scanner_test.duckdb") as storage:
    tickers = storage._con.execute("SELECT DISTINCT ticker, exchange FROM tbl_prices").fetchall()

    def count(res_freq, lookback, tol):
        total = 0
        for t, e in tickers:
            prices = storage.read_prices(t, e)
            if not prices.empty:
                df_run = resample_ohlcv(prices, res_freq) if res_freq else prices
                if df_run.empty: continue
                s = box_breakout.compute_series(df_run, lookback=lookback, touch_tolerance=tol, compression_threshold=2.0, duration_pct=0.5)
                total += s["direction"].isin(["buy", "sell"]).sum()
        return total

    print("Weekly tests:")
    for tol in [0.10, 0.15, 0.20]:
        print(f"  tol={tol} -> {count('W-FRI', 104, tol)}")

    print("Monthly tests:")
    for tol in [0.20, 0.30, 0.40, 0.50]:
        print(f"  tol={tol} -> {count('ME', 240, tol)}")
