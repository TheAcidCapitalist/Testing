# Scoring & Ranking Spec

Synthesized from the `Read me - Signals` "Combo" section and the `Settings` tab.
Implementation contract for `src/scanner/scoring.py`. This layer runs **after** all
indicators have computed; it consumes their outputs and produces the ranked report.

## Stage 1 — Normalize each indicator to 0–1

Every indicator value is mapped to a 0–1 scale where **low = buy, high = sell**
(matching the source's combo convention). The combo is an arithmetic mean of these
normalized values, with the exceptions below.

| Indicator | Normalization |
|-----------|---------------|
| RSI | `rsi / 100` (already 0–100; lower RSI = more oversold = buy) |
| Daily Trend — Divergence | scale slope into 0–1 via its buy/sell thresholds; positive slope → toward 0 (buy), negative → toward 1 (sell) |
| Daily Trend — Contrarian | same scaling, inverted mapping |
| Bollinger — Normal | **three-value scheme**: 0.25 if buy breakout, 0.75 if sell breakout, 0.5 if neutral |
| Bollinger — Contrarian | same three-value scheme on the contrarian signal |
| MAV Breakout | three-value scheme: 0.25 buy breakout, 0.75 sell breakout, 0.5 none |
| Box Breakout | three-value scheme: 0.25 fresh bullish breakout, 0.75 fresh bearish, 0.5 none |
| Stochastic | scale %K into 0–1; only contributes when its divergence condition is met, else 0.5 (neutral) |
| Volatility (confirmation) | use percentile directly (low vol percentile = confirmation) |
| Volume (confirmation) | use **(1 − percentile)** — high volume percentile is bullish-confirming, so subtract from 1 |
| ~~Open Interest~~ | dropped in v1 — not in the mean |

> The three-value scheme is taken verbatim from the source: "To normalize the
> Bollinger band z score, just three values are assigned to it: 0.25 if there is a
> buy breakout, 0.75 if it has a sell breakout & 0.5 if it is in neutral territory."
> Volume/OI deduction from 1 is likewise from the source.

## Stage 2 — Combo score

```
combo_score = mean(normalized values of the indicators in the combination)
```

- Low combo score → buy signal. High combo score → sell signal.
- Buy/sell zone thresholds (from Settings, "Security-wise Technical Indicator"):
  - `combo_score < 0.3` → confirmed **Buy Zone**
  - `combo_score > 0.7` → confirmed **Sell Zone**
  - `0.3 – 0.7` → neutral
- The source allows up to **three** named combinations. v1: implement a single
  default combination over all 8 trade indicators + the 2 active confirmation
  indicators, but keep the combination definition data-driven (a list of indicator
  names + optional weights) so more can be added without code changes.

## Stage 3 — Ranking

The raw combo score is necessary but not sufficient for a useful daily report. The
ranked output augments it with four factors:

1. **Agreement** — how many of the 8 trade indicators agree on direction. A name
   where 6/8 fire long is stronger than one where 3/8 do, even at the same combo
   score. This is the headline ranking signal.
2. **Combo score magnitude** — distance from 0.5; how decisively into the buy or sell
   zone the score sits.
3. **Confirmation strength** — the volatility and volume confirmation states.
   - **Volume: demote, do not remove.** A signal with low-volume (volume percentile
     below `confirm_threshold`) is ranked lower but still appears. This is the
     explicit v1 decision — volume is an important factor, not a hard gate.
   - Volatility in the reversal zone (percentile > 0.7) likewise demotes.
4. **Breakout recency** — for MAV Breakout, Bollinger, and Box Breakout, a more
   recent breakout ranks above a stale one (the source: "priority is given to the
   most recent breakout"). Use `days_since_breakout`.

Suggested ranking key (tune in Phase E):

```
rank_score =  w_agree   * (agreement_count / 8)
            + w_magnitude * abs(combo_score - 0.5) * 2
            + w_confirm  * confirmation_multiplier      # 1.0 confirmed, 0.6 demoted
            - w_staleness * min(days_since_breakout, 20) / 20
```

Weights start equal-ish; the backtest decides the real values. Rank long candidates
and short candidates separately.

## Output of the scoring layer

A ranked table (one row per ticker that has any signal), written to DuckDB and
serialized to JSON for the report + dashboard:

```
ticker, name, exchange, region, sector,
direction,                 # buy / sell
combo_score,
rank_score,
agreement_count,           # 0–8
signals_firing,            # list of indicator names firing in `direction`
vol_confirmation,          # confirm / neutral / reject
volume_confirmation,       # confirm / neutral / reject  (demotes if not confirm)
days_since_breakout,       # min across breakout-type indicators, or null
as_of_date
```

The report (`report/excel.py`, `report/email.py`) takes the top N long and top N
short by `rank_score`. The LLM briefing layer (`agent/briefing.py`) reads this same
JSON — it never re-derives anything, it only explains what's already ranked.

## What the scoring layer must NOT do

- It must not call an LLM. The LLM briefing is a separate, later stage that consumes
  this layer's JSON output.
- It must not fetch data or recompute indicators — it consumes indicator outputs.
- It must not apply hard filters that silently drop signals. Liquidity filtering
  (ADV / market-cap floors) happens in the **universe** layer, before indicators
  run — see `universe.md`. Volume confirmation here only *demotes*.
