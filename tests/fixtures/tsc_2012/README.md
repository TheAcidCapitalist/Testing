# TSC Macro Dashboard — May 2012 Fixtures

Ground-truth fixtures extracted from `TSC Macro - Technical Dashboard - 31 May 2012.xlsm`,
specifically the `Daily` tab. Used to validate that the indicator engine reproduces the
spreadsheet's computed values for known tickers.

## Files

- `<TICKER>_ohlcv.csv` — daily OHLCV history (~287 rows ending 2012-08-29), one per ticker.
  Columns: `date, open, high, low, close, volume, open_interest, realized_vol`.
  Date is ISO-format string. Rows ordered most-recent first (as the spreadsheet stores them).
- `expected_indicators.csv` — one row per ticker. Contains the indicator values the
  spreadsheet computed for the most recent date (`as_of`). Engine should reproduce these
  within tolerance.

## Tickers covered

| Short | Full ticker     | Asset class |
|-------|-----------------|-------------|
| WTI   | CL1 Comdty      | Commodity (WTI crude front-month) |
| GOLD  | GC1 Comdty      | Commodity (gold front-month) |
| EUR   | EURUSD Curncy   | FX |
| JPY   | USDJPY Curncy   | FX |
| GBP   | GBPUSD Curncy   | FX |

No equity tickers — the original dashboard was a macro tool. Indicator math is
asset-class-agnostic, so futures/FX fixtures validate the engine for stocks too.

## Indicator column reference

| Column              | Meaning |
|---------------------|---------|
| `daily_trend`       | Daily Trend indicator value (21d MAV slope, % change) |
| `daily_trend_prev`  | Previous day's Daily Trend value |
| `dt_flag`           | DT signal flag (0 = no signal, ±1 = buy/sell) |
| `rsi`               | RSI(14) latest value |
| `rsi_prev`          | RSI(14) previous day |
| `rsi_flag`          | RSI signal flag |
| `mav_narrow_pct`    | MAV bandwidth percentile (60d window). Signal fires when < 0.30 |
| `mav_breakout_flag` | MAV breakout direction (+1 up, −1 down, 0 none) |
| `mav_days_since`    | Days since most recent MAV breakout |
| `bollinger_z`       | Bollinger Z-score (21d, current price vs 21d MA / 21d σ) |
| `bollinger_days`    | Days since Bollinger breakout |
| `bollinger_time`    | Bollinger time indicator |
| `vol_percentile`    | Realized volatility percentile (180d window). Confirms if < 0.30 |
| `oi_percentile`     | Open interest percentile (180d window). Confirms if > 0.70 |
| `volume_percentile` | Volume percentile (180d window). Confirms if > 0.70 |

## Validation strategy

For each ticker:

1. Load `<TICKER>_ohlcv.csv` (note: reverse to chronological order before computing).
2. Run the indicator engine across the full history.
3. Take the indicator values at the latest date.
4. Assert they match the corresponding row in `expected_indicators.csv` within tolerance:
   - Continuous values (RSI, daily_trend, bollinger_z, percentiles): `abs_tol=1e-3`
   - Discrete flags (dt_flag, rsi_flag, mav_breakout_flag): exact match
   - Day counters (mav_days_since, bollinger_days): exact match

## Source

`Daily` tab of `TSC Macro - Technical Dashboard - 31 May 2012.xlsm`. The spreadsheet's
filename references May 2012, but the data in the Daily tab runs through 2012-08-29 —
the dashboard was updated through that date before being archived.
