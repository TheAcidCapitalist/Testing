# Project Status

**As of:** 2026-05-20
**Branch:** `main`

---

## Where we are

Phases A, B, and C are complete. The full deterministic pipeline runs end-to-end:

```
candidates() → fetch OHLCV → store → run indicators → normalize → combo score → rank → store
```

The CLI command `uv run scanner run-daily --universe sample` executes the complete pipeline
against 15 US large-caps, writes both DuckDB layers, and prints a verification table.
It requires `EODHD_API_KEY` in `.env`.

**Phase D (report + email + GitHub Actions) is next.**

---

## What works

| Component | File | Tests |
|-----------|------|-------|
| 11 indicators (RSI, Bollinger ×2, Daily Trend ×2, Stochastic, MAV Breakout, Box Breakout, MAV Diff Z, Volatility, Volume) | `src/scanner/indicators/` | 274 (5 xfail) |
| EODHD client with 20/day budget enforcement | `src/scanner/data/eodhd.py` | 46 |
| DuckDB two-layer storage | `src/scanner/data/storage.py` | 34 |
| Universe loader (sample scope; us/global deferred) | `src/scanner/data/universe.py` | 35 |
| Scoring: normalize + combo + rank | `src/scanner/scoring.py` | — |
| Orchestrator + CLI | `src/scanner/cli.py` | 45 |

**Total: 319 pass, 5 xfailed**

---

## What doesn't exist yet

| Component | Needed for |
|-----------|-----------|
| `src/scanner/report/excel.py` | Phase D |
| `src/scanner/report/email.py` | Phase D |
| `src/scanner/report/dashboard_json.py` | Phase D |
| `src/scanner/agent/briefing.py` | Phase D |
| `.github/workflows/` (wired up) | Phase D |
| Backtest harness | Phase E |
| `us` / `global` universe scopes | Open decision #14 (metadata source) |

---

## Key known constraints

- **Free EODHD tier: 20 API calls/day.** Each ticker costs 1 call. The sample universe
  (15 tickers) fits; fetch idempotency means re-runs don't burn the budget on already-stored data.
- **Bulk EOD is paywalled** (HTTP 423 confirmed). The pipeline uses per-ticker EOD only.
- **`us`/`global` scopes** raise `ProductionScopeUnavailable`. They are blocked on the
  metadata-source decision (open decision #14 in `spec/phase-c-plan.md`). yfinance is one
  free option once the decision is made.
- **5 xfailed tests** (all `test_mav_breakout.py`): `breakout_flag` and `days_since_breakout`
  can't be reproduced from our 287-bar fixture — the original Bloomberg data spanned years.
  The formula is correct; the synthetic tests cover the firing logic.
- **Daily resolution only.** Multi-timeframe (weekly/monthly) is a v2 upgrade documented in
  `spec/multi-timeframe.md`.

---

## Running it

```bash
# Install deps
~/bin/uv sync --dev

# Run all tests
~/bin/uv run pytest                          # 319 pass, 5 xfail

# Lint
~/bin/uv run ruff check src tests            # 0 errors

# Daily scan (requires EODHD_API_KEY in .env)
~/bin/uv run scanner run-daily --universe sample

# Write ranked CSV instead of stdout
~/bin/uv run scanner run-daily --universe sample --output-path out.csv
```

---

## File map (short version)

```
spec/           canonical specs — source of truth, governs all implementation
src/scanner/
  indicators/   11 pure-function indicators, auto-registered via REGISTRY
  data/
    eodhd.py    EODHD client + CallBudget
    universe.py two-stage universe loader
    storage.py  DuckDB persistence (tbl_prices, tbl_indicator_outputs, tbl_combo_results)
  scoring.py    normalize() + score_tickers() + 3 seeded combinations
  cli.py        run_daily() orchestrator + CLI entry-point
  report/       Excel, email, dashboard JSON (scaffolded, untested — Phase D)
tests/
  fixtures/tsc_2012/   2012 Bloomberg ground-truth (5 tickers, newest-first CSVs)
  fixtures/synthetic/  hand-built fixtures for each indicator
CLAUDE.md       full AI session context (authoritative — read this first)
ROADMAP.md      phase plan with completion status
STATUS.md       this file
```
