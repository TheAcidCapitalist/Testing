# Multi-Timeframe Scan Spec

Design contract for multi-timeframe range-breakout analysis. Derived from
`reference/range_breakout_scanner_brief.docx`. Ambiguities from the brief are
explicitly noted below — each is flagged for user resolution before implementation.

---

## Design principle — layer separation

The indicators in `src/scanner/indicators/` are **single-timeframe pure functions**.
They receive a bar series at whatever resolution and return signals. They have no
knowledge of wall-clock time, bar period, or resolution.

Multi-timeframe analysis is implemented at two higher layers:

1. **Orchestrator (Phase C addendum):** Resamples daily OHLCV into weekly, monthly,
   and quarterly bars. Calls indicators over each resampled series independently.
   Stores outputs keyed by `(ticker, exchange, date, indicator_name, resolution)`.

2. **Scoring layer (Phase D addendum):** After per-resolution outputs are stored,
   checks whether the same indicator fires in the same direction across multiple
   resolutions on the same as-of date and computes an `mtf_alignment_score`. Tickers
   with multi-resolution alignment rank higher — see `spec/scoring.md §Stage 3`.

**Do not put resampling logic or resolution-awareness inside any indicator.**
`compute()` and `compute_series()` must remain resolution-agnostic.

---

## Resampling

Use `pandas.DataFrame.resample()` on the bar DatetimeIndex. Standard anchoring:

| Resolution | Resample key | OHLCV aggregation |
|------------|-------------|-------------------|
| Weekly | `'W-FRI'` — week ending Friday | open=first, high=max, low=min, close=last, volume=sum |
| Monthly | `'ME'` — month-end | open=first, high=max, low=min, close=last, volume=sum |
| Quarterly | `'QE'` — quarter-end | open=first, high=max, low=min, close=last, volume=sum |

The resampled DataFrame is passed directly to `indicator.compute(df, **params)`. The
indicator sees a standard OHLCV DataFrame and applies its logic without knowing the
resolution.

**Incomplete periods (in-progress bars):** On weekly or monthly bars captured mid-
period (e.g. capturing a weekly bar on Wednesday), the current bar may be incomplete.
Whether to include or trim it is controlled by the `confirmed_close` parameter in Box
Breakout — see open question #5 in `spec/indicators.md §8`.

---

## Default scan modes

Three named scan modes from the brief, each defining a `(lookback_bars, resolution)`
pair. All three stay within the brief's 40–300-bar guideline:

| Mode | Lookback (bars) | Resolution | Approx. bars | Min. history needed | Pattern type |
|------|----------------|-----------|-------------|---------------------|--------------|
| `short_term` | 60 | Daily | 60 | ~1 year daily | Flags, VCP, short bases |
| `medium_term` | 104 | Weekly | 104 | ~2.5 years daily | Stage-1 accumulation, multi-month bases |
| `long_term` | 240 | Monthly | 240 | ~22 years daily | Secular breakouts, generational ranges |

Any `(lookback_bars, resolution)` pair is valid as a custom scan mode.

---

## History requirements per mode

| Mode | Min. daily bars needed | Notes |
|------|----------------------|-------|
| `short_term` | ~250 daily bars | 60 lookback + ~200 warmup for trend-filter MA |
| `medium_term` | ~650 daily bars | 104 weekly bars × 5 days + warmup |
| `long_term` | ~5,500 daily bars | 240 monthly bars × ~22 trading days + warmup |

The `long_term` mode requires roughly 22 years of price history per ticker. This
exceeds what EODHD's paid tier provides for many non-US tickers. See **Data source
implications** below.

---

## Per-resolution storage

When multi-timeframe scanning is active, `tbl_indicator_outputs` gains a `resolution`
column extending the primary key:

```
PRIMARY KEY (ticker, exchange, date, indicator_name, resolution)
resolution ∈ {'daily', 'weekly', 'monthly', 'quarterly'}
```

The `date` field holds the **bar close date** at the given resolution (last trading
day of the week/month/quarter). This enables cross-resolution queries: "find all
tickers where `box_breakout` fired `buy` on both `daily` and `weekly` resolution as
of the same as-of date."

**Backward compatibility:** The v1 storage schema (`tbl_indicator_outputs` in
`src/scanner/data/storage.py`) does not yet have a `resolution` column. The Phase C
MTF addendum adds the column with `DEFAULT 'daily'` so existing v1 rows are
unaffected. The scoring layer treats missing `resolution` as `'daily'`.

---

## Multi-timeframe alignment

For a given `(ticker, as-of-date, indicator, direction)`, the `alignment_count` is
the number of resolutions on which that indicator fired `direction` on that date:

```
alignment_count = count of resolutions in {daily, weekly, monthly, quarterly}
                  where indicator fired `direction` on as_of_date
```

- `alignment_count = 1`: single-resolution signal (standard v1 behaviour)
- `alignment_count = 2`: two resolutions agree — elevated conviction
- `alignment_count = 3`: three resolutions agree — highest conviction

The `mtf_alignment_score` term in the `rank_score` formula (see `spec/scoring.md`)
translates this count into a ranking boost.

**⚠ Open question #7 — Alignment scoring shape.**
The brief states: "stocks breaking out across multiple timeframes simultaneously are
the highest-conviction setups — the output should rank these at the top." How to score
this is not specified. Options:

- **(A) Additive / fractional:** `mtf_alignment_score = alignment_count / n_resolutions`
  (0.33 for 1/3, 0.67 for 2/3, 1.0 for 3/3). Linear and tunable via `w_mtf`.
- **(B) Multiplicative bonus:** `rank_score × (1 + α)^(alignment_count − 1)` where `α`
  is a per-resolution bonus. Rewards full 3-resolution alignment non-linearly.
- **(C) Hard tier:** `alignment_count ≥ 2` → promote to a "high-conviction" tier in the
  report regardless of raw `rank_score`. Orthogonal to the ranking formula.
- **(D) Bonus only at alignment ≥ 2:** Single-resolution signals are unaffected; bonus
  applies only when two or more resolutions agree.

**Resolution pending user decision.** Placeholder for the Phase D implementation:
option (A) with `w_mtf = 0.0` initially, tuned in Phase E.

---

## Open question #6 — Bar-count table inconsistency

The brief's lookback/resolution mapping table includes:

> "3–10 years weekly: 36–520 bars"

At the upper end (10 years weekly = 520 bars), this exceeds the stated 40–300-bar
guideline. The brief does not resolve this tension. Options:

- **(A) Soft heuristic:** Accept windows above 300 bars. The 40–300 guideline is
  advisory; for multi-decade weekly scans, more bars may be acceptable.
- **(B) Hard cap at 300 bars:** For any weekly scan beyond ~5.75 years, use only the
  most recent 300 weekly bars.
- **(C) Resolution switch at 5 years:** 3–5 years on weekly (156–260 bars, within range);
  5–10 years on monthly (60–120 bars, also within range). This is the approach the
  three default modes above take implicitly.

**Resolution pending user decision.** The default modes use option (C).

---

## Data source implications

| Mode | History | EODHD free (20 calls/day) | EODHD paid (~$20–50/mo) | Stooq (free bulk) | Norgate (~$30/mo) |
|------|---------|--------------------------|------------------------|-------------------|-------------------|
| `short_term` | ~1 yr | ❌ rate-limited | ✅ | ✅ | ✅ |
| `medium_term` | ~2.5 yr | ❌ rate-limited | ✅ | ✅ | ✅ |
| `long_term` | ~22 yr | ❌ rate + history | ⚠ (~15–20 yr, large-cap US only) | ✅ (~30 yr, most US tickers) | ✅ (70+ yr, incl. delisted) |
| Aspirational 50-year | 50 yr | ❌ | ❌ | ⚠ (select tickers) | ✅ |

**Key flags against the production data-source decision (open in `spec/phase-c-plan.md`):**

1. **`short_term` and `medium_term`** are satisfied by EODHD paid tier — no change to
   the current Phase C plan.

2. **`long_term` (22-year monthly)** requires a second data source for most tickers.
   The brief recommends **Stooq** for long-history US equity data: free, bulk ZIP
   downloads, no API key, no rate limits, ~30 years of EOD history. A Stooq ingestion
   path (one-time bulk load + daily delta append) would enable `long_term` for US
   equities at no cost.

3. **Norgate Data** (~$30/mo) covers 70+ years including delisted stocks (no
   survivorship bias). Required if aspirational 50-year analysis is in scope.

4. The brief explicitly warns: "EODHD's free tier is only 20 calls/day — avoid."
   This is consistent with the Phase C probe findings (`spec/eodhd-probe-notes.md`).

5. **Decision point for Phase C addendum:** Is a Stooq ingestion path in scope for
   v1? Adding Stooq adds one-time complexity (different data format) but enables the
   `long_term` scan mode for US equities. Deferring it limits Phase C to `short_term`
   and `medium_term` on EODHD paid.
