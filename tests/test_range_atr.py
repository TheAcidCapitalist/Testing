from scanner.data.storage import Storage
import pandas as pd
import numpy as np

with Storage("data/scanner_test.duckdb") as storage:
    prices = storage.read_prices("AAPL", "US")

    high = prices["high"].values
    low = prices["low"].values
    close = prices["close"].values
    
    # calc ATR
    n = len(prices)
    tr = np.empty(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    atr14 = pd.Series(tr).rolling(14).mean().values
    
    # For daily, lookback=60
    # Let's find places where close breaks out of 60-day high
    for i in range(60, 2000):
        box_high = np.max(high[i-60:i])
        box_low = np.min(low[i-60:i])
        box_range = box_high - box_low
        
        # just print a few random bars' range-to-ATR
        ratio = box_range / atr14[i-1]
        if i % 100 == 0:
            print(f"Bar {i}: range={box_range:.2f}, ATR={atr14[i-1]:.2f}, ratio={ratio:.2f}")

