"""Universe loader for signal-scanner.

Defines which securities the scanner runs over.  Filtering is split into two
explicit stages so the loader never needs to drive ingestion:

  Stage 1 — candidates(scope, ...)
    Returns a DataFrame of (ticker, exchange, name, currency, market_cap_usd,
    sector, region) for all tickers in the scope that pass the market-cap filter.
    For the 'sample' scope: metadata is embedded as constants (Option A from
    spec/phase-c-plan.md §7.1).  Zero API calls consumed.
    For 'us' and 'global': fetches symbol lists, filters common stock, and
    attaches metadata from yfinance.

  Stage 2 — apply_post_ingest_filters(candidates, storage, ...)
    Filters the candidate list using data that only becomes available *after*
    prices have been stored: bar count, latest close, and average daily value.
    Called by the orchestrator after it has fetched and stored prices.

The loader never writes to storage and never fetches data.  It tells the
orchestrator which tickers to fetch (stage 1) and which to retain after prices
exist (stage 2).

See spec/universe.md for the filter rationale and spec/phase-c-plan.md §7.1 for
the metadata-source strategy that determines how 'us' / 'global' will be built.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal
import logging

import pandas as pd

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from scanner.data.storage import Storage

# Column order for the candidates DataFrame (and tbl_universe).
CANDIDATE_COLUMNS = [
    "ticker",
    "exchange",
    "name",
    "currency",
    "market_cap_usd",
    "sector",
    "region",
]

# ~15 liquid US names covering 9 GICS sectors.  All are large-caps with years
# of price history, so they clear the min_history_bars=250 and min_market_cap
# filters comfortably in normal operation.
#
# Market caps are approximate and will drift — refresh manually when the gap
# becomes large or a ticker is added.  The user can revise this list per
# spec/phase-c-plan.md open decision #2.
#
# Exchange code "US" is the EODHD free-tier exchange identifier for US stocks
# (confirmed in Probe 2).  Non-US coverage is deferred (open decision #4).
#
# Note: BRK-B uses a dash; the EODHD endpoint path is /api/eod/BRK-B.US.
SAMPLE_UNIVERSE: list[dict] = [
    {
        "ticker": "AAPL",  "exchange": "US",
        "name": "Apple Inc.",           "currency": "USD",
        "market_cap_usd": 3.0e12, "sector": "Technology",             "region": "North America",
    },
    {
        "ticker": "MSFT",  "exchange": "US",
        "name": "Microsoft Corp.",      "currency": "USD",
        "market_cap_usd": 3.1e12, "sector": "Technology",             "region": "North America",
    },
    {
        "ticker": "GOOGL", "exchange": "US",
        "name": "Alphabet Inc.",        "currency": "USD",
        "market_cap_usd": 2.1e12, "sector": "Technology",             "region": "North America",
    },
    {
        "ticker": "NVDA",  "exchange": "US",
        "name": "NVIDIA Corp.",         "currency": "USD",
        "market_cap_usd": 2.8e12, "sector": "Technology",             "region": "North America",
    },
    {
        "ticker": "META",  "exchange": "US",
        "name": "Meta Platforms Inc.",  "currency": "USD",
        "market_cap_usd": 1.4e12, "sector": "Communication Services", "region": "North America",
    },
    {
        "ticker": "AMZN",  "exchange": "US",
        "name": "Amazon.com Inc.",      "currency": "USD",
        "market_cap_usd": 2.0e12, "sector": "Consumer Discretionary", "region": "North America",
    },
    {
        "ticker": "JPM",   "exchange": "US",
        "name": "JPMorgan Chase & Co.", "currency": "USD",
        "market_cap_usd": 6.9e11, "sector": "Financials",             "region": "North America",
    },
    {
        "ticker": "BRK-B", "exchange": "US",
        "name": "Berkshire Hathaway B", "currency": "USD",
        "market_cap_usd": 9.8e11, "sector": "Financials",             "region": "North America",
    },
    {
        "ticker": "JNJ",   "exchange": "US",
        "name": "Johnson & Johnson",    "currency": "USD",
        "market_cap_usd": 3.8e11, "sector": "Healthcare",             "region": "North America",
    },
    {
        "ticker": "UNH",   "exchange": "US",
        "name": "UnitedHealth Group",   "currency": "USD",
        "market_cap_usd": 4.8e11, "sector": "Healthcare",             "region": "North America",
    },
    {
        "ticker": "PG",    "exchange": "US",
        "name": "Procter & Gamble Co.", "currency": "USD",
        "market_cap_usd": 3.9e11, "sector": "Consumer Staples",       "region": "North America",
    },
    {
        "ticker": "XOM",   "exchange": "US",
        "name": "Exxon Mobil Corp.",    "currency": "USD",
        "market_cap_usd": 4.9e11, "sector": "Energy",                 "region": "North America",
    },
    {
        "ticker": "CAT",   "exchange": "US",
        "name": "Caterpillar Inc.",     "currency": "USD",
        "market_cap_usd": 1.8e11, "sector": "Industrials",            "region": "North America",
    },
    {
        "ticker": "LIN",   "exchange": "US",
        "name": "Linde plc",           "currency": "USD",
        "market_cap_usd": 2.2e11, "sector": "Materials",              "region": "North America",
    },
    {
        "ticker": "NEE",   "exchange": "US",
        "name": "NextEra Energy Inc.",  "currency": "USD",
        "market_cap_usd": 1.4e11, "sector": "Utilities",              "region": "North America",
    },
]


# ── Exception ─────────────────────────────────────────────────────────────────


# ── Stage 1: candidate universe ───────────────────────────────────────────────


def candidates(
    scope: Literal["sample", "us", "global"],
    *,
    client: EODHDClient | None = None,
    storage: Storage | None = None,
    min_market_cap_usd: float = 750_000_000,
    metadata_refresh_days: int = 30,
    max_metadata_fetches_per_run: int = 500,
) -> pd.DataFrame:
    """Return candidate tickers that pass the market-cap filter.  Stage 1.

    For ``'sample'``: returns the curated list with embedded metadata,
    filtered by ``min_market_cap_usd``.  Zero API calls consumed.

    For ``'us'`` and ``'global'``: fetches symbol lists from EODHD, filters for 
    Common Stock, and retrieves market cap, sector, and country metadata 
    from yfinance. Missing metadata is excluded.
    The yfinance metadata is cached in storage (tbl_universe) and refreshed 
    according to ``metadata_refresh_days``.
    Note: The loader does NOT cap or throttle. A large global scope becomes 
    a rolling multi-day backfill handled by the orchestrator's budget logic.

    Returns a DataFrame with columns ``CANDIDATE_COLUMNS``:
    ticker, exchange, name, currency, market_cap_usd, sector, region.

    The returned rows are the tickers the orchestrator should fetch prices for.
    Post-ingestion filtering (ADV, min_price, min_history_bars) is applied in
    stage 2 via :func:`apply_post_ingest_filters` after prices are in storage.
    """
    if scope == "sample":
        df = pd.DataFrame(SAMPLE_UNIVERSE)[CANDIDATE_COLUMNS]
        return df[df["market_cap_usd"] >= min_market_cap_usd].reset_index(drop=True)

    if scope in ("us", "global"):
        if client is None or storage is None:
            raise ValueError(f"client and storage are required for scope '{scope}'")

        exchanges = ["US"] if scope == "us" else ["US", "LSE", "TO", "PA", "XETRA", "TSE", "HK", "ASX"]
        df_list = []
        for ex in exchanges:
            try:
                df_ex = client.fetch_symbol_list(ex)
                # Drop PINK and keep only Common Stock
                df_ex = df_ex[(df_ex["Type"] == "Common Stock") & (df_ex["Exchange"] != "PINK")]
                df_list.append(df_ex)
            except Exception as exc:
                logger.warning("Failed to fetch symbol list for exchange %s: %s", ex, exc)
                continue

        if not df_list:
            return pd.DataFrame(columns=CANDIDATE_COLUMNS)

        all_symbols = pd.concat(df_list, ignore_index=True)
        all_symbols = all_symbols.rename(columns={
            "Code": "ticker",
            "Exchange": "exchange",
            "Name": "name",
            "Currency": "currency",
            "Country": "region"
        })

        # Load cache
        cached = storage.read_universe()
        
        now = pd.Timestamp.now()
        threshold = now - pd.Timedelta(days=metadata_refresh_days)
        valid_cache = cached[cached["updated_at"] >= threshold].copy()

        # Merge current symbols with valid cache to find what needs fetching
        merged = all_symbols.merge(
            valid_cache[["ticker", "exchange", "market_cap_usd", "sector"]],
            on=["ticker", "exchange"],
            how="left"
        )

        missing_mask = merged["market_cap_usd"].isna() | merged["sector"].isna()
        missing_tickers = merged.loc[missing_mask, "ticker"].tolist()

        if missing_tickers:
            from scanner.data.yfinance_meta import fetch_yfinance_meta
            
            # Throttle-respecting batch limit
            batch = missing_tickers[:max_metadata_fetches_per_run]
            
            logger.info("Fetching yfinance metadata for %d missing/expired tickers...", len(batch))
            new_meta = fetch_yfinance_meta(batch)
            
            # Prepare rows to write to cache
            cache_updates = all_symbols[all_symbols["ticker"].isin(batch)].copy()
            new_meta_renamed = new_meta.rename(columns={
                "market_cap": "market_cap_usd",
                "country": "fetched_region"
            })
            cache_updates = cache_updates.merge(new_meta_renamed, on="ticker", how="left")
            
            # Update region if yfinance returned one
            cache_updates["region"] = cache_updates["fetched_region"].combine_first(cache_updates["region"])
            
            # Ensure exactly CANDIDATE_COLUMNS are written to storage
            cache_updates = cache_updates[CANDIDATE_COLUMNS]
            
            # Persist to cache (including Nones for failed fetches)
            storage.write_universe(cache_updates)
            
            # Update `merged` DataFrame so we can evaluate these tickers in this run
            merged_idx = merged.set_index("ticker")
            cache_updates_idx = cache_updates.set_index("ticker")
            merged_idx.update(cache_updates_idx)
            merged = merged_idx.reset_index()

        # Choice flagged in prompt: exclude tickers that failed to return a market cap
        # because the liquidity filters cannot be correctly applied without it.
        final_df = merged.dropna(subset=["market_cap_usd"]).copy()
        
        # Apply min_market_cap filter
        final_df = final_df[final_df["market_cap_usd"] >= min_market_cap_usd].copy()

        final_df = final_df[CANDIDATE_COLUMNS]
        return final_df.reset_index(drop=True)

    raise ValueError(f"Unknown scope '{scope}'.  Must be one of: sample, us, global.")


# ── ADV helper ────────────────────────────────────────────────────────────────


def compute_adv(
    storage: Storage,
    ticker: str,
    exchange: str,
    *,
    window: int = 20,
) -> float | None:
    """Compute mean daily dollar-volume over the last *window* stored bars.

    Dollar-volume per bar = close × volume (standard ADV approximation).
    If fewer than *window* bars are stored, uses all available bars.
    Returns ``None`` if no prices are stored for the ticker.
    """
    df = storage.read_prices(ticker, exchange)
    if df.empty:
        return None
    return _adv_from_prices(df, window=window)


def _adv_from_prices(prices: pd.DataFrame, *, window: int) -> float:
    """Compute mean daily dollar-volume from a prices DataFrame."""
    recent = prices.tail(window)
    return float((recent["close"] * recent["volume"]).mean())


# ── Stage 2: post-ingestion filters ──────────────────────────────────────────


def apply_post_ingest_filters(
    candidates_df: pd.DataFrame,
    storage: Storage,
    *,
    min_avg_daily_value: float = 5_000_000,
    min_price: float = 1.0,
    min_history_bars: int = 250,
    adv_window: int = 20,
) -> pd.DataFrame:
    """Filter candidates using data that only exists after price ingestion.  Stage 2.

    Must be called *after* the orchestrator has fetched and stored prices for
    every candidate in ``candidates_df``.  Applies three filters:

    * **min_history_bars** — stored bar count >= min_history_bars.
    * **min_price** — most recent ``close`` >= min_price.
    * **min_avg_daily_value** — mean daily dollar-volume over ``adv_window``
      bars >= min_avg_daily_value (Option E from spec/phase-c-plan.md §7.1:
      computed from stored prices, zero extra API calls).

    A candidate with no stored prices at all is always excluded.

    Returns a DataFrame with the same columns as *candidates_df* and a fresh
    integer index.  Returns an empty DataFrame (with correct columns) if no
    candidates pass.

    Parameters
    ----------
    candidates_df:
        Output of :func:`candidates` (stage 1).
    storage:
        The active Storage instance.  Read-only — this function never writes.
    min_avg_daily_value:
        Minimum mean daily dollar-volume in USD (default $5M).
    min_price:
        Minimum latest close price in USD (default $1.00).
    min_history_bars:
        Minimum number of stored price bars (default 250).
    adv_window:
        Number of recent bars to average for ADV (default 20).
    """
    kept: list[pd.Series] = []

    for _, row in candidates_df.iterrows():
        ticker: str = row["ticker"]
        exchange: str = row["exchange"]
        prices = storage.read_prices(ticker, exchange)

        if prices.empty:
            continue  # no data yet — exclude

        # ── History-bar count ─────────────────────────────────────────────
        if len(prices) < min_history_bars:
            continue

        # ── Latest close price ────────────────────────────────────────────
        latest_close = prices["close"].iloc[-1]
        if pd.isna(latest_close) or float(latest_close) < min_price:
            continue

        # ── Average daily value (computed from stored prices) ─────────────
        adv = _adv_from_prices(prices, window=adv_window)
        if adv < min_avg_daily_value:
            continue

        kept.append(row)

    if not kept:
        return pd.DataFrame(columns=candidates_df.columns)
    return pd.DataFrame(kept).reset_index(drop=True)
