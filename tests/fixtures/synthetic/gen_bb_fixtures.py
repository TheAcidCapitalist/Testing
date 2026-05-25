import pandas as pd
import numpy as np
from pathlib import Path

SYN_DIR = Path(__file__).parent

def _base_df(n_volatile: int, n_tight: int, n_post: int = 1) -> pd.DataFrame:
    total = n_volatile + n_tight + n_post
    dates = pd.date_range("2012-01-01", periods=total, freq="B")
    
    vol_high = 105.0
    vol_low = 95.0
    
    tight_high = 101.0
    tight_low = 99.0
    
    df = pd.DataFrame({
        "date": dates,
        "open": 100.0,
        "high": vol_high,
        "low": vol_low,
        "close": 100.0,
        "volume": 1000.0,
    })
    
    tight_start = n_volatile
    tight_end = n_volatile + n_tight
    df.loc[tight_start:tight_end-1, "high"] = tight_high
    df.loc[tight_start:tight_end-1, "low"] = tight_low
    
    return df

def make_csv(name, df):
    df.to_csv(SYN_DIR / f"box_{name}.csv", index=False)

PRE_BARS = 50
TIGHT_BARS = 60
BREAKOUT_IDX = PRE_BARS + TIGHT_BARS

# 1-9 generated as before
df = _base_df(PRE_BARS, TIGHT_BARS, 1)
df.loc[BREAKOUT_IDX, ["open", "high", "low", "close", "volume"]] = [100.0, 106.0, 100.0, 105.0, 2000.0]
make_csv("flat_then_breakout_up", df)

df = _base_df(PRE_BARS, TIGHT_BARS, 1)
df.loc[BREAKOUT_IDX, ["open", "high", "low", "close", "volume"]] = [100.0, 100.0, 94.0, 95.0, 2000.0]
make_csv("flat_then_breakout_down", df)

df = _base_df(PRE_BARS, TIGHT_BARS, 1)
df.loc[BREAKOUT_IDX, ["open", "high", "low", "close", "volume"]] = [100.0, 106.0, 100.0, 105.0, 2000.0]
make_csv("and_gate_no_compression", df)

df = _base_df(PRE_BARS, TIGHT_BARS, 1)
df.loc[PRE_BARS, "high"] = 120.0
df.loc[PRE_BARS, "low"] = 80.0
df.loc[BREAKOUT_IDX, ["open", "high", "low", "close", "volume"]] = [100.0, 125.0, 100.0, 125.0, 2000.0]
make_csv("and_gate_no_proximity", df)

df = _base_df(PRE_BARS, TIGHT_BARS, 1)
df.loc[PRE_BARS + 30, "high"] = 120.0
df.loc[PRE_BARS + 30, "low"] = 80.0
df.loc[BREAKOUT_IDX, ["open", "high", "low", "close", "volume"]] = [100.0, 125.0, 100.0, 125.0, 2000.0]
make_csv("duration_shortfall", df)

df = _base_df(PRE_BARS, TIGHT_BARS, 1)
df.loc[BREAKOUT_IDX, ["open", "high", "low", "close", "volume"]] = [100.0, 106.0, 100.0, 105.0, 1000.0]
make_csv("vol_absent", df)

df = _base_df(PRE_BARS, TIGHT_BARS, 6)
df.loc[BREAKOUT_IDX, ["open", "high", "low", "close", "volume"]] = [100.0, 106.0, 100.0, 105.0, 2000.0]
df.loc[BREAKOUT_IDX+1:, ["open", "high", "low", "close", "volume"]] = [105.0, 106.0, 104.0, 105.0, 1000.0]
make_csv("recency_expired", df)

df = _base_df(PRE_BARS, TIGHT_BARS, 1)
df.loc[BREAKOUT_IDX, ["open", "high", "low", "close", "volume"]] = [100.0, 106.0, 99.0, 100.0, 2000.0]
make_csv("false_poke", df)

df = pd.DataFrame({
    "date": pd.date_range("2012-01-01", periods=150, freq="B"),
    "open": 100.0 + np.arange(150),
    "high": 101.0 + np.arange(150),
    "low": 99.0 + np.arange(150),
    "close": 100.0 + np.arange(150),
    "volume": 1000.0
})
make_csv("trending_no_box", df)

# 10. range_filling_base
# A base spanning [PRE_BARS, PRE_BARS+TIGHT_BARS-1] where the price cycles 102 -> 100 -> 98 -> 100
# Box high = 102, Box low = 98.
df = _base_df(PRE_BARS, TIGHT_BARS, 1)
cycle = [102.0, 100.0, 98.0, 100.0]
for i in range(TIGHT_BARS):
    idx = PRE_BARS + i
    val = cycle[i % 4]
    df.loc[idx, ["open", "high", "low", "close"]] = [val, val, val, val]

# At the breakout bar, it breaks above the box high (102).
df.loc[BREAKOUT_IDX, ["open", "high", "low", "close", "volume"]] = [102.0, 106.0, 102.0, 105.0, 2000.0]
make_csv("range_filling_base", df)
