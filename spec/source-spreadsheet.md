# Source Spreadsheet — Reference Extraction

Plain-text extraction of the three reference tabs from
`reference/tsc-macro-dashboard-2012-05-31.xlsm`. This is the **raw source of truth**.
`indicators.md`, `scoring.md`, and `universe.md` are the synthesized, cleaned-up specs
derived from this file — when they conflict, this file wins, but note that this file
itself contains internal contradictions (see "Known discrepancies" at the bottom).

---

## Tab 1 — `Read me - Indicators`

### Trend Indicators

These indicators give a Buy or Sell signal.

**Relative Strength Indicator (RSI)**
A long position is taken when RSI crosses the lower threshold from below; a short
position is taken when RSI crosses the upper threshold from above.
Inputs: RSI days = 14. Buy when crosses above 35, Exit Buy 50. Sell when crosses
below 65, Exit Sell 50.

**Stochastic Oscillator**
Trading signals are generated on the simultaneous satisfaction of two conditions.
Long position when: (1) Stochastic oscillator (K) is below the lower threshold, and
(2) there is a bullish divergence — price is touching lower lows, but stochs are
touching higher lows. Short position when: (1) K is above the upper threshold, and
(2) there is a bearish divergence — price touching higher highs, stochs touching
lower highs. Stochastic highs/lows are calculated when K% crosses d% from
below/above. Inputs: K% days = 14, d% days = 5. Buy Below 35, Exit Buy Above 50.
Sell above 65, Exit Sell Below 50.

**MAV Breakout**
Signalled when four conditions are satisfied simultaneously:
1. MAV band widths have narrowed considerably. MAV band width = difference of highest
   and lowest of the 3 MAVs. Degree of narrowing measured by percentile of the
   bandwidth. Trade generated when MAV band width percentile is under the threshold.
2. Breakout: for upside, the 21-day LOW MAV slope has turned positive (for downside,
   the 21d HIGH MAV has turned negative).
3. For upside breakout, stochastics have turned positive (opposite for downside).
4. Daily close: for upside breakout, price has had a daily close above the top band
   (opposite for downside).
Inputs: MAV1 = 21, MAV2 = 34, MAV3 = 55. MAV Percentile window = 60. MAV narrowing
threshold = 0.3. Stochastic K% window = 21. None of the MAV conditions are used for
exiting the trade.

**Daily Trend — Divergence**
Based on the percentage change in the current MAV of the series. A long position is
taken when DT crosses above the slope threshold level, and vice-versa for short.
Trending concept: buy when MAV slope is positive and continues in that region.
Inputs: MA window = 21. Buy when crosses above 0.005, Exit Buy 0. Sell when crosses
below -0.005, Exit Sell 0. Series available for last, high, and low price.

**Daily Trend — Contrarian**
Based on the percentage change in the current MAV. A short position is taken when DT
crosses below the slope threshold level. Concept: MAV slope was high for a long time
and has started to decline. Inputs: MA window = 21. Buy when crosses above -0.005,
Exit Buy 0. Sell when crosses below 0.005, Exit Sell 0.

**Bollinger Band — Normal**
Based on the number of standard deviations the current price is above/below the
moving average. Long when the Bollinger z-score rises above the higher threshold.
Short when it falls below the lower z-score threshold. Inputs: Number of days for
z-score = 21. Standard deviation threshold = 1.5. Contrarian view threshold = 0.25.

**Bollinger Band — Contrarian**
A new threshold level equal to (standard deviation minus threshold) generates
contrarian signals. Long when the z-score falls below this new threshold level.
Short when it rises above it. Inputs: Number of days = 21. Standard deviation
threshold = 1.5. Contrarian view threshold = 0.25.

### Confirmation Indicators

These don't give a Buy/Sell signal. They give: (a) Trend confirmatory signal —
whether a given buy/sell signal is strong enough to trade on; (b) Trend reversal
signal — a good time to exit a trade due to uncertainty or change in trend.

**MAV Difference Z Score**
Two MAVs used to assess the exponential move/acceleration of trend direction. The
Z-score of the difference between the two MAVs gives the magnitude of the move.
Trend reversal is taken when the sign of the z-score changes. Inputs: MAV1 = 20,
MAV2 = 50, History for Z Score calculation = 180. Only used in backtesting as an
exit signal.

**Volatility**
Realized volatility forms the basis of uncertainty. High RV = uncertainty, low RV =
confirmation of trend. A trade is taken only when the percentile of current
volatility is below the trend confirmation threshold. If above the trend reversal
threshold, the current trade is exited. Inputs: History for percentile = 180.
Trend Confirmation = 0.3, Trend Reversal = 0.7.

**Volume**
Low volume = uncertainty, high volume = confirmation. A trade is taken only when the
percentile of current volume is above the trend confirmation threshold. If below the
trend reversal threshold, the trade is exited. Inputs: History for percentile = 180.
Trend Confirmation = 0.7, Trend Reversal = 0.3.

**Open Interest**
Percentile of the change in Open Interest (period for change is user-defined),
inferred similarly to Volume. Trade taken only when percentile is above the trend
confirmation threshold. If below the trend reversal threshold, the trade is exited.
Inputs: Number of Days = 7, History for percentile = 180. Trend Confirmation = 0.7,
Trend Reversal = 0.3.
> NOTE for the equities build: Open Interest is a futures/options field with no
> single-stock equivalent. It is DROPPED in v1. See indicators.md.

---

## Tab 2 — `Read me - Signals`

### Trade Alerts
Whenever any trade indicator crosses the buy/sell threshold, a trade alert is
generated on the 'Trade Alerts' tab. Colour scheme: a new buy signal and a new sell
signal are shown with the current value of the indicator; old (un-reversed) signals
for the same security are also shown. Signal confirmation signs for Volatility, Open
Interest & Volume: `1` = confirmed by all three; `0` = neither confirmed nor
rejected; `-1` = rejected. Max 1000 trades shown at a time per interval (hourly,
daily, weekly).

### Dashboard
All indicators can be coloured in 10 shades reflecting trade signals — dark green =
strong buy, dark red = strong sell, yellow = neutral. The dashboard supports:
change frequency (2-Hourly / Daily / Weekly); ascending/descending arrangement of
indicator values (except MAV breakout & Bollinger band, where priority is given to
the most recent breakout); filter by asset class; sort by a chosen indicator.

MAV breakout dashboard symbols: bandwidth narrowed; broke out on the upside one day
ago (buy); bandwidth widened; broke out on the downside two days ago (sell);
bandwidth neither narrowed nor widened; no breakout.
Volatility/Volume/OI symbols: confirmed; neither; rejected.

### Combo
The user can combine two or more indicators into a combined signal (up to three
combinations). A lower combined value indicates a buy; a larger value indicates a
sell. Methodology: a simple arithmetic mean of the indicator values (normalized to a
0–1 scale), with exceptions. To normalize the Bollinger band z-score, three values
are assigned: 0.25 if there is a buy breakout, 0.75 if a sell breakout, 0.5 if
neutral. Volume and Open Interest are used after deducting them from 1 (e.g. Volume
at the 70th percentile contributes 0.30).

### Settings Tab notes (from this tab)
All inputs for trade and confirmatory indicators are entered in the Settings sheet.
New instruments are added under the 'Instruments Added' heading (Asset Class, Short
name, Price ticker, Volatility ticker, OI ticker, Volume ticker). "Click to update
securities" replicates instruments into Daily/Hourly/Weekly sheets and pulls data.
Roll type adjustment (futures): Contract selection = Bloomberg Default / Relative to
expiration / With Active future. Roll-over adjustment = None / Difference / Average.
Default is "Active futures" + "Difference".

### Data problems noted
Hourly: aggregate OI, realized volatility and aggregate volume are not available
intraday — Daily values are substituted for Hourly. Low data points: if a security
has fewer data points than the configured history, computation uses what's
available. Fixed income: for some FI securities, yield data is used instead of bond
price.

---

## Tab 3 — `Settings`

### Indicator Settings (the configured parameter values)

| Indicator | Parameter | Value |
|-----------|-----------|-------|
| Daily Trend (DT - Last Price) | Moving Average | 21 |
| Daily Trend | Slope Threshold — Hourly | 0.00125 |
| Daily Trend | Slope Threshold — Daily | 0.005 |
| Daily Trend | Slope Threshold — Weekly | 0.015 |
| RSI Trend Strength | No. of Days | 14 |
| RSI Trend Strength | Lowest category | < 30 |
| RSI Trend Strength | Highest category | > 70 |
| RSI Trend Strength | Mid category | 30 – 70 |
| Stochastic | %K | 14 |
| Stochastic | %D | 5 |
| MAV Breakout | MAV 1 | 21 |
| MAV Breakout | MAV 2 | 34 |
| MAV Breakout | MAV 3 | 55 |
| MAV Breakout | MAV breakout Threshold | 0.4 |
| MAV Breakout | History of Analysis | 250 |
| MAV Breakout | K window | 14 |
| Bollinger Breakout | Number of days | 21 |
| Bollinger Breakout | Standard deviation | 1.5 |
| Bollinger Breakout | Breakout History | 30 |
| Delivered Volatility Trend | Time Period | 1M |
| Delivered Volatility Trend | History for analysis | 180 |
| Delivered Volatility Trend | Volatility Threshold to confirm trend | 0.3 |
| Delivered Volatility Trend | RV threshold for Reversing trend | 0.7 |
| Open Interest Trend | Number of days | 7 |
| Open Interest Trend | History for analysis | 180 |
| Open Interest Trend | OI threshold to confirm trend | 0.7 |
| Open Interest Trend | OI threshold for Reversing trend | 0.3 |
| Volume | History for analysis | 180 |
| Volume | Volume threshold to confirm trend | 0.7 |
| Volume | Volume threshold for Reversing trend | 0.3 |
| Security-wise Technical Indicator | Threshold to confirm Buy Zone | 0.3 |
| Security-wise Technical Indicator | Threshold to confirm Sell Zone | 0.7 |

### Inputs for Trade Alerts (trade-entry signal thresholds)

| Indicator | Condition | Value |
|-----------|-----------|-------|
| DT - Hourly - Divergence | Buy when crosses above | 0.00125 |
| DT - Hourly - Divergence | Sell when crosses below | -0.00125 |
| DT - Daily - Divergence | Buy when crosses above | 0.005 |
| DT - Daily - Divergence | Sell when crosses below | -0.005 |
| DT - Weekly - Divergence | Buy when crosses above | 0.015 |
| DT - Weekly - Divergence | Sell when crosses below | -0.015 |
| Stochastic | Buy Below | 20 |
| Stochastic | Sell above | 80 |
| MAV Breakout | MAV breakout Threshold | 0.4 |
| MAV Breakout | MAV percentile window | 250 |
| MAV Breakout | K window | 14 |
| Bollinger - Contrarian | Lower Band | -1.5 |
| Bollinger - Contrarian | Upper Band | 1.5 |
| Bollinger - Contrarian | Contrarian View Threshold | 0.25 |
| Volatility | Trend Confirmation | 0.3 |
| Volume | Trend Confirmation | 0.7 |
| OI | Trend Confirmation | 0.7 |

### Roll Types / Adjustments (futures only — not used in the equities build)
Roll Type: Bloomberg Default (B), Relative to Expiration (R), With Active Future (A).
Adjustment: None (N), Difference (D), Average (W).

### Instruments Added (the original universe — ~150 instruments)
Asset classes used: **Comm, Fx, Fi, Equity, Swaps**. The original universe is almost
entirely futures, FX pairs, government bond yields, and rates/swaps — NOT single
stocks. The "Equity" entries are equity *index futures* (ES1, NKY, SPX, etc.), not
individual equities. Full ticker list is in the spreadsheet's Settings tab; it is not
reproduced here because the equities build defines its own universe (see
universe.md). The asset-class field concept is retained; the taxonomy is replaced.

---

## Known discrepancies (Read me - Indicators vs. Settings tab)

The two tabs disagree on several parameters. Claude Code must pick one source per
parameter and document the choice in `indicators.md`. Recommended resolution noted:

| Parameter | Read me - Indicators | Settings tab | Recommended |
|-----------|---------------------|--------------|-------------|
| RSI buy/sell thresholds | 35 / 65 (cross), exit 50 | 30 / 70 (categories) | Use 35/65 cross for the trade signal; 30/70 is a separate "trend strength" categorization |
| Stochastic buy/sell thresholds | Buy below 35 / Sell above 65 | Buy below 20 / Sell above 80 | Use the Settings "Inputs for Trade Alerts" values (20/80) — that tab explicitly governs trade alerts |
| MAV narrowing threshold | 0.3 | 0.4 | Use 0.4 (Settings governs trade alerts); make it a parameter, tune in backtest |
| MAV percentile window | 60 | 250 | Use 250 (Settings) |
| MAV Stochastic K window | 21 | 14 | Use 14 (Settings) |

All such values must be **parameters with defaults**, never hardcoded — final values
get set in the Phase E backtest, not by reading this table.
