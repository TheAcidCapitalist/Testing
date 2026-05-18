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

## Combinations — definition and registry

A **combination** is a named, data-driven selection of indicators to include in a
combo score run. Subset selection is first-class: a combination containing a single
indicator ("only Box Breakout") flows through the same Stage 2 and Stage 3 logic as
the full default — just over a smaller set. The CLI and report selection mechanism is
a Phase D concern; this section defines the combination contract that `scoring.py`
implements.

### Definition schema

A combination is a name plus a list of indicator entries. Each entry identifies an
indicator by its `NAME` in the indicator registry, with an optional weight that
defaults to `1.0`. The combo score is a weighted mean over the listed indicators.

```
{
  "name":       <string>,                         # unique key, e.g. "default"
  "indicators": [
    { "indicator": <name>, "weight": <float> },   # weight defaults to 1.0
    ...
  ]
}
```

Equal weights (all `1.0`) reproduce the arithmetic mean from the original
spreadsheet. A weight of `0.0` is equivalent to omitting the indicator.

### Seeded combinations

| Name | Indicators | Purpose |
|------|------------|---------|
| `default` | All 8 trade indicators + Volatility + Volume | Full daily scan — matches the original spreadsheet |
| `breakout_family` | MAV Breakout, Bollinger Normal, Box Breakout | Tickers with an active breakout-type signal |
| `mean_reversion` | RSI, Bollinger Contrarian, Stochastic | Oversold/overbought mean-reversion candidates |

Additional combinations can be added without code changes by extending the
combination registry (a config file or DuckDB table, specified in Phase C).

### How selection feeds into Stage 2 and Stage 3

**Stage 2** computes `combo_score` as a weighted mean over the indicators listed in
the selected combination.

**Stage 3** counts agreement over the **trade indicators in the selected
combination**. The denominator N in `agreement_count / N` equals the number of trade
indicators in the combination — not always 8. For `breakout_family` (3 trade
indicators), N = 3.

Confirmation indicators (Volatility, Volume) contribute to `combo_score` when
included in the combination. Regardless of whether they appear in the combination,
their stored outputs always feed the `confirmation_multiplier` in Stage 3 — demotion
for low volume or high volatility is applied whenever their data is available in
storage.

## Stage 2 — Combo score

```
combo_score = sum(weight_i × norm_i  for i in selected combination) / sum(weight_i)
```

- Low combo score → buy signal. High combo score → sell signal.
- Buy/sell zone thresholds (from Settings, "Security-wise Technical Indicator"):
  - `combo_score < 0.3` → confirmed **Buy Zone**
  - `combo_score > 0.7` → confirmed **Sell Zone**
  - `0.3 – 0.7` → neutral
- The selected combination determines which indicators participate — see
  **Combinations** above.

## Stage 3 — Ranking

The raw combo score is necessary but not sufficient for a useful daily report. The
ranked output augments it with four factors:

1. **Agreement** — how many of the N trade indicators in the selected combination
   agree on direction. A name where 6/8 fire long is stronger than one where 3/8 do,
   even at the same combo score. This is the headline ranking signal.
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
5. **Multi-timeframe alignment** — a ticker where the same indicator (or combination)
   fires in the same direction on multiple bar resolutions simultaneously has higher
   conviction than a single-resolution fire. Per `spec/multi-timeframe.md`: daily +
   weekly + monthly alignment is the highest-conviction setup. The `mtf_alignment_score`
   encodes how many resolutions agree. This factor is only active when the multi-
   timeframe scan modes are running; single-resolution (v1 daily-only) runs have
   `mtf_alignment_score = 0.0` and `w_mtf` is zeroed.

   **⚠ Open question #7 — Alignment scoring shape:** See `spec/multi-timeframe.md`
   for the full set of options (additive, multiplicative, hard-tier, bonus-only-at-2).
   **Resolution pending user decision.** Placeholder: additive option (A), `w_mtf`
   tuned in Phase E.

Suggested ranking key (tune in Phase E):

```
rank_score =  w_agree     * (agreement_count / N)        # N = trade indicators in combination
            + w_magnitude * abs(combo_score - 0.5) * 2
            + w_confirm   * confirmation_multiplier       # 1.0 confirmed, 0.6 demoted
            - w_staleness * min(days_since_breakout, 20) / 20
            + w_mtf       * mtf_alignment_score           # 0.0 in v1 daily-only runs
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
agreement_count,           # 0–N (N = trade indicators in selected combination)
signals_firing,            # list of indicator names firing in `direction`
vol_confirmation,          # confirm / neutral / reject
volume_confirmation,       # confirm / neutral / reject  (demotes if not confirm)
days_since_breakout,       # min across breakout-type indicators, or null
as_of_date
```

The report (`report/excel.py`, `report/email.py`) takes the top N long and top N
short by `rank_score`. The LLM briefing layer (`agent/briefing.py`) reads this same
JSON — it never re-derives anything, it only explains what's already ranked.

### Per-indicator storage — hard requirement

`data/storage.py` must persist the **per-indicator normalized value and raw
indicator result for every ticker on every run date**, keyed by
`(ticker, exchange, indicator_name, date)` — not only the final combo score.

This is what makes subset combination cheap: re-scoring a `breakout_family` combo
over stored data is a query and weighted mean over existing rows, not a re-run of
the indicator engine on raw OHLCV. The ranked output table above is derived from
these per-indicator rows and is always recomputeable from them.

**Resolution dimension (MTF addendum):** When multi-timeframe scan modes are active,
the storage key gains a `resolution` field:
`(ticker, exchange, date, indicator_name, resolution)` where
`resolution ∈ {'daily', 'weekly', 'monthly', 'quarterly'}`.
The v1 daily-only schema uses `resolution='daily'` as the implicit value. The column
is added in the Phase C MTF addendum with `DEFAULT 'daily'` for backward
compatibility with existing v1 rows. The `mtf_alignment_score` is computed by
querying across `resolution` values for the same `(ticker, exchange, date, indicator_name)`.
See `spec/multi-timeframe.md §Per-resolution storage` for the full schema change.

## What the scoring layer must NOT do

- It must not call an LLM. The LLM briefing is a separate, later stage that consumes
  this layer's JSON output.
- It must not fetch data or recompute indicators — it consumes indicator outputs.
- It must not apply hard filters that silently drop signals. Liquidity filtering
  (ADV / market-cap floors) happens in the **universe** layer, before indicators
  run — see `universe.md`. Volume confirmation here only *demotes*.
