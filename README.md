# Signal Scanner

A scheduled equity signal scanner that computes a suite of technical indicators,
scores tickers on a weighted combo formula, and delivers a ranked Excel report
by email every morning before market open.

## Features

- **8 indicators** — RSI, Stochastic, MAV Breakout, Daily Trend, Bollinger Bands,
  Volatility, Volume, MAV Diff Z-score
- **DuckDB cache** — incremental OHLCV storage, no redundant API calls
- **Excel report** — colour-coded workbook with per-indicator sheets + summary
- **Daily email** — HTML email with top-20 table + attachment
- **Dashboard** — single-file HTML artifact (`dashboard/artifact.html`) that
  reads `data/latest.json`; share without a server
- **GitHub Actions** — daily scan at 06:00 ET + PR CI

## Quick start

```bash
# 1. Clone & enter
git clone <repo> && cd signal-scanner

# 2. Install uv (if not already)
curl -Lsf https://astral.sh/uv/install.sh | sh

# 3. Install dependencies
uv sync

# 4. Configure secrets
cp .env.example .env
# → edit .env with your EODHD_API_KEY and SMTP credentials

# 5. Run a scan
uv run scanner scan
```

## Project structure

```
signal-scanner/
├── CLAUDE.md                 ← AI session context
├── pyproject.toml
├── .env.example
├── spec/                     ← canonical specs
│   ├── indicators.md
│   ├── scoring.md
│   └── universe.md
├── src/scanner/
│   ├── indicators/           ← one file per indicator
│   ├── data/                 ← EODHD client + DuckDB
│   ├── report/               ← Excel, email, JSON
│   ├── scoring.py
│   └── cli.py
├── tests/
└── dashboard/artifact.html
```

## Configuration

All runtime configuration is via environment variables (or `.env`).
See `.env.example` for the full list and `CLAUDE.md` for a quick reference.

## Indicator specs

See [`spec/indicators.md`](spec/indicators.md) for precise definitions of every
signal, including lookback periods and signal thresholds.

## Scoring

See [`spec/scoring.md`](spec/scoring.md) for the combo formula and ranking rules.

## License

MIT
