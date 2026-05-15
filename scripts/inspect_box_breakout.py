"""Human-readable Box Breakout summary across the five TSC 2012 fixtures.

This is an eyeball-check script — not a pytest test, no assertions.
Run with:
    ~/bin/uv run python scripts/inspect_box_breakout.py

For each ticker it prints:
  - Every completed valid box found: start date, end date, length, high/low.
  - Whether the bar immediately after the box was a breakout (+1/-1/none).
  - Latest-bar direction and days_since_breakout.

GBP had real consolidation in the 2012 data — its output is the best informal
sanity check (should show at least one meaningful box).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from scanner.indicators.box_breakout import compute, compute_series

TSC_DIR = Path(__file__).parent.parent / "tests" / "fixtures" / "tsc_2012"
TICKERS = ["WTI", "GOLD", "EUR", "JPY", "GBP"]

# Default params (same as compute() defaults)
PARAMS = dict(
    min_congestion_bars=15,
    max_range=0.06,
    range_metric="pct",
    atr_window=14,
    breakout_buffer=0.25,
    breakout_recency=3,
)


def load_tsc(ticker: str) -> pd.DataFrame:
    df = pd.read_csv(TSC_DIR / f"{ticker}_ohlcv.csv", parse_dates=["date"])
    return df.iloc[::-1].reset_index(drop=True)


def summarise(ticker: str, df: pd.DataFrame) -> None:
    series = compute_series(df, **PARAMS)
    latest = compute(df, **PARAMS)

    print(f"{'═'*60}")
    print(f"  {ticker}  ({len(df)} bars,  {df['date'].iloc[0].date()} → {df['date'].iloc[-1].date()})")
    print(f"{'─'*60}")

    # Find breakout events (breakout_dir != 0)
    breakout_bars = series.index[series["breakout_dir"] != 0].tolist()
    if not breakout_bars:
        print("  No completed valid boxes / breakouts found.")
    else:
        for i in breakout_bars:
            d = int(series["breakout_dir"].iloc[i])
            bh = series["box_high"].iloc[i]
            bl = series["box_low"].iloc[i]
            bl_ = series["box_length"].iloc[i]
            direction_str = "▲ BUY" if d == 1 else "▼ SELL"
            date = df["date"].iloc[i].date()
            # Box ran from (i - box_length) to (i - 1)
            box_len = int(bl_) if not pd.isna(bl_) else "?"
            box_start_idx = i - int(bl_) if not pd.isna(bl_) else "?"
            box_start_date = df["date"].iloc[box_start_idx].date() if isinstance(box_start_idx, int) else "?"
            box_end_date = df["date"].iloc[i - 1].date()
            close_at_break = df["close"].iloc[i]
            tightness = (bh - bl) / ((bh + bl) / 2) if bh and bl else "?"
            print(
                f"  {direction_str}  bar {i:>3}  {date}"
                f"  | box [{box_start_date} → {box_end_date}]  len={box_len}"
                f"  | box H={bh:.4f}  L={bl:.4f}  tight={tightness:.4f}"
                f"  | close={close_at_break:.4f}"
            )

    # Also list completed boxes that ended WITHOUT a breakout (quiet exits)
    quiet_boxes = []
    # A quiet box: box_length goes from nan → value in series but breakout_dir stays 0
    # We detect these by looking at bars where the box_active transitions from True to False
    # and breakout_dir = 0 at that transition.
    was_active = False
    for i in range(len(series)):
        active_now = bool(series["box_active"].iloc[i])
        if was_active and not active_now and int(series["breakout_dir"].iloc[i]) == 0:
            # Box ended quietly at bar i
            quiet_boxes.append(i)
        was_active = active_now

    if quiet_boxes:
        print(f"  Quiet box exits (no breakout): {len(quiet_boxes)} instance(s)")

    # Latest-bar summary
    print(f"  Latest bar: direction={latest['direction']!r}  "
          f"signal_value={latest['signal_value']}  "
          f"days_since_breakout={latest['days_since_breakout']}")
    if latest["box_high"] is not None:
        bh, bl = latest["box_high"], latest["box_low"]
        print(f"  Most recent valid box: H={bh:.4f}  L={bl:.4f}  "
              f"length={latest['box_length']}  "
              f"tightness={(bh - bl)/((bh + bl)/2):.4f}")
    print()


def main() -> None:
    for ticker in TICKERS:
        df = load_tsc(ticker)
        summarise(ticker, df)


if __name__ == "__main__":
    main()
