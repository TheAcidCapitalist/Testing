# Dashboard JSON Output Contract

Implementation contract for `src/scanner/report/dashboard_json.py`.
This is the canonical output that every downstream consumer reads:
- **D2** — `report/excel.py` (ranked workbook)
- **D3** — `agent/briefing.py` (Haiku annotation — reads *only* this JSON)
- **D5** — `dashboard/artifact.html` (static HTML dashboard)

## Design principles

1. **Read-only.** The module reads from `Storage` (`read_combo_results_all`,
   `read_indicator_outputs`, `read_universe`) and emits a Python dict /
   JSON file. It never scores, fetches, or re-derives alignment.
2. **Host-agnostic.** Two functions: `build_dashboard_dict()` returns the
   dict; `write_dashboard_json()` writes it to a path. The hosting target
   (repo, gist, object store) is not baked into the contract.
3. **Stdlib only.** Uses `json` from the standard library. No new deps.
4. **Schema-versioned.** `schema_version` in the envelope; bump on breaking
   change. Consumers can gate on it.

## Top-level structure

```json
{
  "meta": { ... },
  "tickers": [ ... ]
}
```

## `meta` — run-level envelope

| Field | Type | Source | Notes |
|-------|------|--------|-------|
| `schema_version` | `string` | Hardcoded `"1.0"` | Bump on breaking change |
| `run_date` | `string` | Param | `"YYYY-MM-DD"` |
| `generated_at` | `string` | `datetime.now(timezone.utc)` | ISO-8601 |
| `scope` | `string` | Param | `"sample"` / `"us"` / `"global"` |
| `combination_name` | `string` | Param | `"default"` etc. |
| `n_tickers_scored` | `int` | `len(tickers)` | Total scored |
| `n_tickers_universe` | `int` | Param | Pre-filter universe size |
| `n_buy` | `int` | Direction count | |
| `n_sell` | `int` | Direction count | |
| `n_neutral` | `int` | Direction count | |

## `tickers` — ranked list

An array of ticker objects, **sorted by `rank_score` descending**. The
emitter imposes this order; `read_combo_results_all` does not guarantee it.

### Per-ticker core fields

All sourced from `tbl_combo_results` columns:

| Field | Type | Notes |
|-------|------|-------|
| `ticker` | `string` | |
| `exchange` | `string` | |
| `date` | `string` | `"YYYY-MM-DD"` |
| `direction` | `string` | `"buy"` / `"sell"` / `"neutral"` |
| `combo_score` | `float` | |
| `rank_score` | `float` | |
| `agreement_count` | `int` | |
| `n_trade_indicators` | `int` | |
| `signals_firing` | `list[string]` | Deserialized from JSON |
| `vol_confirmation` | `string` | `"confirm"` / `"neutral"` / `"reject"` |
| `volume_confirmation` | `string` | Same |
| `days_since_breakout` | `int | null` | |

### `meta` — per-ticker universe metadata

Nested object with fields from `tbl_universe`, joined by `(ticker, exchange)`.
Null-tolerant: if the ticker has no universe row, the block is present with
all-null values. The D3 briefing uses this for context ("AAPL — Apple Inc,
Technology").

```json
"meta": {
  "name": "Apple Inc.",
  "currency": "USD",
  "market_cap_usd": 3000000000000.0,
  "sector": "Technology",
  "region": "North America"
}
```

### `mtf_alignment` — per-ticker MTF alignment block

Populated directly from the three persisted `tbl_combo_results` columns.
Never recomputed.

```json
"mtf_alignment": {
  "resolutions_available": 2,
  "resolutions_aligned": 1,
  "alignment_fraction": 0.5
}
```

### `indicators` — per-indicator detail (tiered)

A dict keyed by indicator name. The detail level depends on the ticker's
`direction`:

- **Non-neutral tickers** (`direction ∈ {"buy", "sell"}`): full `raw_value`
  dict per indicator (all keys from `compute()`), plus `normalized_value`
  and `direction`. This gives the Haiku briefing maximum context for its
  annotation.
- **Neutral tickers**: summary only — `{direction, normalized_value}` per
  indicator. Raw values omitted to keep JSON size down (neutral tickers are
  the tail and rarely surface in the briefing).

Box Breakout MTF outputs appear as flat keys: `box_breakout` (daily),
`box_breakout_weekly`, `box_breakout_monthly`. This matches the internal
`ticker_raw` dict convention. They are present in the indicator detail only
when the resolution was computed and stored.

```json
"indicators": {
  "rsi": {
    "raw_value": {"signal_value": 0.35, "rsi": 35.2},
    "normalized_value": 0.352,
    "direction": "buy"
  },
  "box_breakout": { ... },
  "box_breakout_weekly": { ... },
  "box_breakout_monthly": { ... },
  ...
}
```

## Empty-run case

If no tickers are scored (empty `read_combo_results_all`), the output is:

```json
{
  "meta": { "n_tickers_scored": 0, ... },
  "tickers": []
}
```

All envelope fields are present; `n_buy`, `n_sell`, `n_neutral` are 0.

## API

```python
def build_dashboard_dict(
    storage: Storage,
    run_date: date,
    scope: str,
    combination_name: str = "default",
    n_tickers_universe: int = 0,
) -> dict:
    """Build the canonical dashboard JSON dict from storage."""

def write_dashboard_json(
    data: dict,
    path: str | Path,
) -> Path:
    """Write a dict (from build_dashboard_dict) to a JSON file.
    Returns the resolved Path."""
```
