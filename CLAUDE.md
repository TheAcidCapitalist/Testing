# CLAUDE.md

Context for every Claude Code session in this repo. Read this first.

## Maintaining this file

**This file must stay accurate.** It describes the repo as it *is*, not as it will
be. When you finish a piece of work that changes any of the following, update
CLAUDE.md in the same commit:

- a command becomes real (it now runs and passes) → move it from "Planned" to "Now"
- a directory or module gets created → update "Repo status"
- a phase completes → update "Current status" at the bottom
- a convention changes → fix it here

Updating CLAUDE.md is part of "done," not a separate chore. A CLAUDE.md that lists
commands which fail, or describes code that doesn't exist, is worse than a short one
— it trains everyone to distrust it. If you're unsure whether something exists,
check before writing it down.

## What this is

A deterministic global technical scanner with an LLM-generated daily briefing. Each
weeknight it pulls global EOD equity data, runs a fixed set of technical indicators
across the whole universe, ranks the results, and emails a report (plus an Excel
attachment and a live dashboard) by 6 AM ET.

The indicator logic is ported from the *TSC Macro Technical Dashboard (May 2012)*
spreadsheet — originally a Bloomberg macro tool, now adapted for global equities.

## Core principle: math decides, LLMs annotate

The signal-generation path is **fully deterministic and auditable**. Every signal
traces back to a specific indicator firing on specific data. An LLM never decides
which tickers make the report — it only operates on the *output* of the
deterministic pipeline (explaining, summarizing, contextualizing). If the LLM layer
fails, the report still goes out. Do not introduce an LLM call anywhere in
`indicators/`, `scoring.py`, or `data/` — only in `agent/`.

## Source of truth

The `spec/` directory governs all implementation. When code and spec disagree, the
spec wins; if the spec itself is wrong, fix the spec first, then the code.

- `spec/source-spreadsheet.md` — raw verbatim extraction of the original spreadsheet.
  The ultimate reference. Contains a discrepancy table where the original tabs
  disagree on thresholds.
- `spec/indicators.md` — the implementation contract for all 11 indicators.
- `spec/scoring.md` — normalization, combo score, ranking.
- `spec/universe.md` — what to scan, data source, filters, staging.
- `ROADMAP.md` — the phase plan.

## Spec-deviation rule — important

Some of these indicators are **not standard**. The divergence-aware Stochastic, the
four-condition MAV Breakout, and the FI-uses-yield-not-price quirk are all
intentional. Do not "correct" a formula because it looks unusual. Implement exactly
what the spec says, and validate against the fixture. **If a formula looks wrong, do
not silently fix it — match the fixture. If you cannot match the fixture, stop and
flag it** rather than adjusting the formula until numbers happen to line up.

## Environment

- Python 3.12+, managed with `uv`. Run things as `uv run <cmd>`, not bare `python`.
- Core libraries: `pandas`, `numpy`, `duckdb`. Standard indicators may use
  `pandas-ta`; MAV Breakout, Stochastic divergence, and Box Breakout are hand-written.
- Lint with `ruff`, test with `pytest`.
- No secrets in the repo. Config via `.env` (gitignored); `.env.example` lists the
  keys with placeholder values.

## Indicator contract

Every indicator in `src/scanner/indicators/` is a **pure function** — OHLCV in,
signal out, no I/O (no file reads, no API calls, no DB access):

```python
def compute(df: pd.DataFrame, **params) -> dict:
    # df: chronologically-ascending bars, columns [open, high, low, close, volume],
    #     DatetimeIndex. Returns the latest-bar result:
    #     {"signal_value": float, "direction": "buy"|"sell"|"neutral", ...extras}
    ...

def compute_series(df: pd.DataFrame, **params) -> pd.DataFrame:
    # full per-bar history — needed by the backtest and the v2 context layer.
    ...
```

`scoring.py` consumes the **`compute` (latest-bar)** result for the live daily scan.
`compute_series` exists for the Phase E backtest and the v2 context layer — keep both
in sync (one should be implementable in terms of the other).

All parameters have defaults (from `spec/indicators.md`). Never hardcode a threshold
inside a formula — it must be a named parameter.

Indicators are auto-discovered by `src/scanner/indicators/__init__.py` (registry
pattern). Adding an indicator = drop a file + add its test; nothing else changes.

## Validation gate — non-negotiable

No indicator is "done" until its tests pass.

- Indicators with a 2012 fixture (RSI, Daily Trend, MAV Breakout, Bollinger,
  Volatility, Volume): must reproduce `tests/fixtures/tsc_2012/expected_indicators.csv`
  within tolerance — continuous values `abs_tol=1e-3`, flags and day-counters exact.
  The fixture CSVs are newest-first; reverse to chronological order before computing.
- Indicators without a 2012 fixture (Stochastic divergence logic, Box Breakout):
  validated against hand-built synthetic fixtures in `tests/fixtures/synthetic/`.
  See the Box Breakout section of `spec/indicators.md` for the six required cases.

Write the fixture test FIRST, then implement until green. This catches a
confidently-wrong formula on the first run.

## Conventions

- One file per indicator. Share computation (e.g. the MA slope used by both Daily
  Trend variants, the z-score used by both Bollinger variants) — don't duplicate.
  Shared computation lives in a `_<name>_core.py` module (leading underscore keeps
  it out of the registry). See `_bollinger_core.py` as the established pattern.
- Commit per phase; within Phase B, commit per indicator. Update CLAUDE.md in the
  same commit when the repo state changes (see top of file).

## Workflow

- **One indicator per focused session.** An indicator is a bounded problem; a clean
  context window produces sharper code than one bloated with five previous
  indicators. Start a fresh conversation for each.
- Implementation order (from `spec/indicators.md`): easy first — RSI, Bollinger,
  Daily Trend, Volatility, Volume. **Gnarly last — MAV Breakout, Stochastic
  divergence, Box Breakout.** These three have multiple simultaneous conditions and
  are where a confident-but-wrong implementation is most likely. For these, walk
  through the spec logic in plain English before writing code.
- Do not start the data layer (`data/`) until the engine is green against fixtures.
  The build order exists so the math is validated before any live data is involved.

---

# Repo status

## Exists now

- `spec/` — all four spec files, populated. Source of truth.
- `CLAUDE.md`, `README.md`.
- `tests/fixtures/tsc_2012/` — 5 OHLCV CSVs + `expected_indicators.csv` + README
  (rows are newest-first — reverse before computing).
- **Phase A plumbing (scaffolded):** `pyproject.toml` (uv/hatchling), `.env.example`,
  `.gitignore`, `src/scanner/` package skeleton.
- **Phase B (in progress):**
  - `src/scanner/indicators/rsi.py` — **green ✓** (13 tests). Wilder RSI, matches fixture within 1e-6.
  - `src/scanner/indicators/bollinger_normal.py` + `bollinger_contrarian.py` — **green ✓** (18 tests).
    z = (price − MA) / σ (ddof=1, window=21). Normal: buy when z > +1.5, sell when z < −1.5.
    Contrarian: sell when z > +1.25, buy when z < −1.25. Matches fixture within 1e-6.
  - `src/scanner/indicators/_bollinger_core.py` — shared z-score + days_in_band.
  - `src/scanner/indicators/daily_trend_divergence.py` + `daily_trend_contrarian.py` — **green ✓** (20 tests).
    slope = (MA[t] − MA[t−1]) / MA[t] (21-bar SMA). Divergence: cross signals at ±0.005.
    Contrarian: cross signals at −0.005 (buy) and +0.005 (sell) — different thresholds, not mirrors.
    Matches fixture within machine epsilon.
  - `src/scanner/indicators/_daily_trend_core.py` — shared ma_slope_series (private).
  - `src/scanner/indicators/_percentile.py` — shared Excel PERCENTRANK helper (private).
    count_strictly_below / (n−1), rolling window, min_periods=2. Used by volatility and volume.
  - `src/scanner/indicators/volatility.py` — **green ✓** (14 tests). Confirmation indicator.
    Emits {percentile, state}. State: confirm (<0.3), reject (>0.7). Uses realized_vol column
    when present; falls back to compute_realized_vol (annualized log-return std) for production.
  - `src/scanner/indicators/volume.py` — **green ✓** (14 tests). Confirmation indicator.
    Emits {percentile, state}. State logic opposite Volatility: confirm (>0.7), reject (<0.3).
    Direct volume column — no two-stage computation, no helper like compute_realized_vol.
  - `tests/test_rsi.py`, `tests/test_bollinger.py`, `tests/test_daily_trend.py`,
    `tests/test_volatility.py`, `tests/test_volume.py`.
  - `tests/fixtures/synthetic/rsi_{buy_cross,sell_cross,neutral}.csv`.
  - `tests/fixtures/synthetic/bollinger_{above,below,inside}.csv`.
  - `tests/fixtures/synthetic/dt_{div_buy,div_sell,con_buy,con_sell,flat}.csv`.
  - `tests/fixtures/synthetic/vol_{low_pct,mid_pct,high_pct,short}.csv`.
  - `tests/fixtures/synthetic/volume_{high_pct,mid_pct,low_pct,short}.csv`.
  - `src/scanner/indicators/__init__.py` — registry (auto-discovers non-underscore modules, `NAME` attribute).
  - `src/scanner/scoring.py` — combo + ranking skeleton (not yet green).
- **Phase C scaffolds (exist, untested):** `src/scanner/data/` (eodhd, universe, storage),
  `src/scanner/cli.py`.
- **Phase D scaffolds (exist, untested):** `src/scanner/report/` (excel, email, dashboard
  json), `dashboard/artifact.html`, `.github/workflows/` (daily-scan + ci).
- `tests/conftest.py`, `tests/test_indicators.py`, `tests/test_scoring.py`,
  `tests/test_tsc_regression.py` — tests exist and currently **fail** (indicators not green).

## Does not exist yet

- `spec/source-spreadsheet.md` — the raw verbatim extraction + discrepancy table.
  **Create this before Phase B starts.**
- `ROADMAP.md` — referenced in CLAUDE.md but not yet written.
- `reference/` directory + `.xlsm` — add manually.
- `tests/fixtures/synthetic/` — RSI fixtures exist ✓; Box Breakout and Stochastic divergence fixtures still needed.
- `src/scanner/agent/` — LLM briefing layer (Phase D).
- `data/` directory — gitignored, created at runtime by DuckDB.

## Intended layout

```
src/scanner/
  indicators/   pure functions, auto-registered, no I/O          [scaffolded, not green]
  data/         eodhd client, universe loader, duckdb storage     [scaffolded, not tested]
  scoring.py    combo score + ranking                            [scaffolded, not green]
  report/       excel, email, dashboard json                     [scaffolded, not tested]
  agent/        LLM briefing — the ONLY place an LLM is called    [does not exist]
  cli.py        entrypoint                                       [scaffolded, not tested]
tests/
  fixtures/tsc_2012/    extracted ground truth                   [exists ✓]
  fixtures/synthetic/   hand-built cross scenarios                 [RSI fixtures exist ✓]
reference/      the original .xlsm                               [add manually]
spec/           source of truth                                 [exists ✓]
data/           local DuckDB — gitignored                        [runtime only]
```

## Commands

### Now (verified ✓)

- `~/bin/uv sync --dev` — install all deps (uv is at `~/bin/uv`; add to PATH for convenience).
- `~/bin/uv run pytest tests/test_rsi.py` — 13 tests pass.
- `~/bin/uv run pytest tests/test_bollinger.py` — 18 tests pass.
- `~/bin/uv run pytest tests/test_daily_trend.py` — 20 tests pass.
- `~/bin/uv run pytest tests/test_volatility.py` — 14 tests pass.
- `~/bin/uv run pytest tests/test_volume.py` — 14 tests pass (fixture + state-logic + short-history + consistency).
- `~/bin/uv run ruff check src tests` — passes with 0 errors.

### Planned (Phase C+)

- `uv run scanner run-daily --universe sample|us|global` — the daily scan.

---

# Current status

**Phase A complete ✓. Phase B in progress — RSI ✓, Bollinger ✓, Daily Trend ✓, Volatility ✓, Volume ✓.**

79 tests green across RSI, Bollinger, Daily Trend, Volatility, and Volume. `~/bin/uv run ruff check src tests` passes.

The Phase B scaffold stubs (other indicator files, scoring.py, test files) are parked
in `_phase_b_stubs/` at the repo root. Do not re-add them until they are
rewritten to actually pass the fixture tests. Each indicator gets its own session:

1. Read this file + `spec/indicators.md` section for the target indicator.
2. Write the fixture test first (`tests/test_<name>.py`).
3. Write `src/scanner/indicators/<name>.py` until the test is green.
4. Commit + update CLAUDE.md (move indicator from "planned" to "exists now").

Next indicator: **MAV Breakout** (`spec/indicators.md` §3) — gnarly, walk through spec logic in plain English first.

_(Update this section when a phase or indicator completes.)_
