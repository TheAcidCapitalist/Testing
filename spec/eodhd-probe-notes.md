# EODHD API Probe Notes

## Consolidated summary (updated after Probe 2)

| Topic | Finding |
|-------|---------|
| Bulk-EOD endpoint | ❌ Blocked (HTTP 423) on free tier |
| Per-ticker EOD endpoint | ✅ Available on free tier — HTTP 200 |
| Symbol-list endpoint | ✅ Available on free tier — HTTP 200; **no market cap / sector / ADV** |
| Rate-limit header bucket | 1200 — confirmed **per-minute sliding window** (NOT per-day) |
| Actual daily call limit | 20 calls/day (EODHD billing layer, not in headers) |
| Per-ticker EOD field names | `date` (YYYY-MM-DD string), `open`, `high`, `low`, `close`, `adjusted_close`, `volume` (integer) |
| Symbol-list fields | `Code`, `Name`, `Country`, `Exchange`, `Currency`, `Type`, `Isin` only |
| Market cap / sector source | Not available from symbol-list; requires fundamentals endpoint (1 call/ticker) |

---

## Probe 1

**Date:** 2026-05-16
**Endpoint probed:** bulk-EOD (`/api/eod-bulk-last-day/GSPC`)
**API key tier:** Free
**Session budget consumed:** 1 call

---

## Bulk-EOD endpoint — availability on free tier

**Result: NO. Bulk endpoint is prohibited on the free tier.**

### Request

```
GET https://eodhd.com/api/eod-bulk-last-day/GSPC?api_token=<key>&fmt=json
```

### HTTP response

```
HTTP/2 423
server: nginx/1.19.10
content-type: text/html; charset=utf-8
cache-control: no-cache, private
date: Sat, 16 May 2026 23:17:17 GMT
x-ratelimit-limit: 1200
x-ratelimit-remaining: 1199
access-control-allow-credentials: true
access-control-allow-methods: GET, POST, PUT, DELETE, OPTIONS
access-control-allow-headers: Accept,Authorization,Cache-Control,Content-Type,DNT,...

Bulk requests are prohibited for free users. Please, contact our support team: support@eodhistoricaldata.com
```

### Status code interpretation

HTTP 423 (Locked) — the endpoint exists and the key is valid; access is
plan-gated, not an auth failure. The error message is unambiguous.

---

## Rate-limit headers

| Header | Value |
|--------|-------|
| `x-ratelimit-limit` | 1200 |
| `x-ratelimit-remaining` | 1199 (after 1 call) |

**Note on the 1200 figure:** The pre-probe plan assumed 20 calls/day based on
EODHD's documented free-tier limit. The response header shows 1200 as the limit
bucket. The time window for this bucket is not stated in the headers (no
`x-ratelimit-reset` or `x-ratelimit-window` header was returned). Possibilities:

- 1200/day (the "20/day" figure from the plan was wrong or outdated)
- 1200 per some rolling window (hourly? — 1200/hr ≈ 20/min, which would be
  consistent with the 2/min per-minute limit if that limit is separate)
- The "20/day" limit may be enforced at a different layer (account dashboard)
  and not reflected in these headers

**This must be clarified before the client build.** A per-ticker endpoint probe
(the next session's first call) will show whether `x-ratelimit-remaining`
decrements to 1198, confirming the counter is shared across all endpoint types.

---

## Architecture impact

The entire Phase C daily-refresh strategy was based on 1 bulk call covering the
whole exchange. That is not possible on the free tier.

**Revised daily-refresh approach for the test phase:**

Every ticker in the narrow universe requires an individual per-ticker EOD call.
At ~15 tickers:

| Budget scenario | Calls/day | Notes |
|-----------------|-----------|-------|
| If limit = 20/day | 15 ticker + overhead = ~18 | tight but fits |
| If limit = 1200/day | 15 ticker + overhead = negligible | comfortable |

Either way, 15 tickers is feasible. The budget arithmetic in `spec/phase-c-plan.md`
§3.2 assumed 1 bulk call for daily refresh; that must be revised to 15 per-ticker
calls. The steady-state cost rises from ≤4 to ~17 calls/day under the 20/day
scenario.

**The CallBudget ceiling** in `phase-c-plan.md` §5.2 was set to 20 as a hard
limit. That figure should be treated as provisional until the actual daily window
for the 1200-bucket is confirmed.

---

## What was NOT probed (deferred to next session)

The session budget allowed one call. The following remain open and should each
consume one call in the next probe session:

1. **Per-ticker EOD endpoint** — confirm it responds on the free tier and
   document the response shape: field names, types, date format, adjusted close
   column name, whether volume is included.
   ```
   GET /api/eod/AAPL.US?api_token=<key>&period=d&from=2025-01-01&fmt=json
   ```

2. **Exchange symbol-list endpoint** — confirm availability and document shape:
   whether `MarketCapitalization`, `Sector`, and average volume are included in
   the response (open decision #5 from phase-c-plan.md).
   ```
   GET /api/exchange-symbol-list/US?api_token=<key>&type=common_stock&fmt=json
   ```

3. **Rate-limit window confirmation** — the second per-ticker call will show
   whether `x-ratelimit-remaining` drops to 1198, confirming the shared counter.
   A call made ~60 seconds later would help determine if the window resets per
   minute or per day.

---

## Open decisions updated by this probe

From `spec/phase-c-plan.md` §7:

| # | Decision | Update |
|---|----------|--------|
| 3 | Does the EODHD free tier support the bulk-EOD endpoint? | **Resolved: NO.** HTTP 423. |
| 7 | Rate-limit bucket size and window | **Partially resolved:** limit = 1200; window unknown. |

Decision #3 is closed. The Phase C plan's daily-refresh architecture must be
revised from "1 bulk call per exchange" to "1 per-ticker call per ticker."

---

## Probe 2

**Date:** 2026-05-18
**Endpoints probed:** per-ticker EOD, exchange symbol-list, rate-limit window
**API key tier:** Free
**Session budget consumed:** 3 calls (calls numbered relative to this session)

---

### Call 1 — Per-ticker EOD endpoint

**Request:**
```
GET https://eodhd.com/api/eod/AAPL.US?api_token=<key>&period=d&from=2025-01-01&fmt=json
Timestamp: 2026-05-18 20:31:46 UTC
```

**HTTP response headers:**
```
HTTP/2 200
x-ratelimit-limit: 1200
x-ratelimit-remaining: 1199
content-type: application/json
```

**Result: ✅ Available on free tier.**

**Response shape:** JSON array, ascending date order (earliest bar first). Each element:

```json
{
  "date":           "2025-05-19",   // string, YYYY-MM-DD
  "open":           207.91,         // float
  "high":           209.48,         // float
  "low":            204.26,         // float
  "close":          208.78,         // float
  "adjusted_close": 207.955,        // float — NOTE: "adjusted_close", not "adj_close"
  "volume":         46140500        // integer
}
```

**Field notes:**
- Seven fields total — all OHLCV plus adjusted close.
- The adjusted close column is named `adjusted_close` (not `adj_close` — storage.py and
  the EODHD client must use this exact name when mapping to the `adj_close` DuckDB column).
- `volume` is a bare integer (no nulls observed on AAPL).
- No `exchange` or `ticker` field in the row itself — those come from the URL path.
- Date string format is ISO 8601 (`YYYY-MM-DD`), parseable directly by `pd.to_datetime()`.
- Response covers from the requested `from` date forward; no end-date filtering was applied
  in this call (the response ran from 2025-05-19 to 2026-05-18, the most recent trading day).

**Rate-limit observation:**
`x-ratelimit-remaining` was 1199 after this call — the counter had reset since Probe 1
(May 16, also 1199 after its one call). Consistent with a sub-day window.

---

### Call 2 — Exchange symbol-list endpoint

**Request:**
```
GET https://eodhd.com/api/exchange-symbol-list/US?api_token=<key>&type=common_stock&fmt=json
Timestamp: 2026-05-18 20:32:05 UTC (19 seconds after Call 1)
```

**HTTP response headers:**
```
HTTP/2 200
x-ratelimit-limit: 1200
x-ratelimit-remaining: 1198
```

**Result: ✅ Available on free tier.**

**Response shape:** JSON array of 18,462 rows (US common stocks). Each element:

```json
{
  "Code":     "A",
  "Name":     "Agilent Technologies Inc",
  "Country":  "USA",
  "Exchange": "NYSE",
  "Currency": "USD",
  "Type":     "Common Stock",
  "Isin":     "US00846U1016"   // null for some tickers
}
```

**Complete field list: `Code`, `Name`, `Country`, `Exchange`, `Currency`, `Type`, `Isin`.**

**Critical finding — missing metadata fields:**
`MarketCapitalization`, `Sector`, `AvgVolume` (average daily value), and any other
fundamental or liquidity metadata are **absent** from this endpoint's response.

This resolves open decision #5 from `spec/phase-c-plan.md`:
> *Does the EODHD exchange symbol-list endpoint return MarketCapitalization and Sector?*
> **Resolved: NO.** The symbol list is a pure ticker catalog — code, name, exchange,
> currency, type, ISIN. Nothing more.

**Architecture impact:**
The universe loader cannot apply the `min_market_cap=$750M` or sector filters using
the symbol list alone. Options for the client build:

| Option | Calls per universe build | Feasibility on free tier |
|--------|--------------------------|--------------------------|
| (A) Fundamentals endpoint per ticker (`/api/fundamentals/<ticker>.US`) | 1 per ticker (~18k for full US) | ❌ Not feasible on free tier; viable on paid with budget |
| (B) Skip market cap / sector from EODHD; source metadata from a free alternative (e.g. yfinance batch, Stooq, Wikipedia market cap lists) | 0 EODHD calls | ✅ For the narrow test-phase universe |
| (C) Pre-curate the ~15-ticker test-phase universe manually; no market cap filtering needed | 0 calls | ✅ For test phase specifically |

**Recommendation for test phase:** Option (C) — use a manually curated universe, bypassing the
market cap filter entirely. `min_market_cap` filtering is enforced for the production `us` and
`global` scopes and can be sourced from the fundamentals endpoint there (budget permitting).

**Rate-limit observation:**
`x-ratelimit-remaining` dropped from 1199 to 1198 — confirms the counter is shared
across endpoint types (same bucket as the per-ticker EOD call).

---

### Call 3 — Rate-limit window confirmation (65-second wait)

**Request:**
```
GET https://eodhd.com/api/eod/AAPL.US?api_token=<key>&period=d&from=2026-05-01&fmt=json
Timestamp: ~2026-05-18 20:33:10 UTC (65 seconds after Call 2)
```

**HTTP response headers:**
```
HTTP/2 200
x-ratelimit-limit: 1200
x-ratelimit-remaining: 1199
```

**Key observation:** After 65 seconds, `x-ratelimit-remaining` rose from 1198 to 1199
(not fell to 1197). The bucket partially or fully reset during the 65-second wait.

---

### Rate-limit window analysis

#### Observed sequence

| Event | UTC timestamp | x-ratelimit-remaining |
|-------|---------------|-----------------------|
| Probe 1 — bulk call (HTTP 423) | 2026-05-16 23:17:17 | 1199 |
| Probe 2 — Call 1 (HTTP 200) | 2026-05-18 20:31:46 | 1199 |
| Probe 2 — Call 2 (HTTP 200, +19s) | 2026-05-18 20:32:05 | 1198 |
| Probe 2 — Call 3 (HTTP 200, +65s) | 2026-05-18 20:33:10 | 1199 |

#### Conclusion: the 1200 bucket is a per-minute sliding window

Call 1 and Call 2 are 19 seconds apart. Call 2 showed remaining=1198 (both calls
counted). Call 3 is 65 seconds after Call 2. If the window were per-day or per-hour,
remaining would have shown 1197 (all three calls counting). Instead it showed 1199,
meaning exactly one call was in the window — only Call 3 itself.

The math: at the time of Call 3 (~20:33:10), Call 1 (20:31:46) was ~84 seconds old and
Call 2 (20:32:05) was ~65 seconds old. Both had expired from a 60-second rolling window,
leaving the bucket at 1200 before Call 3 decremented it to 1199.

**The 1200 limit is a per-minute (60-second rolling) throughput cap.**

#### Reconciling the "20 calls/day" figure

The "20 calls per day" from EODHD's documented free-tier limit is a **separate billing-
layer daily quota** not reflected in the `x-ratelimit-*` headers. Two limits apply
simultaneously:

| Limit | Value | Window | Reflected in headers? |
|-------|-------|--------|-----------------------|
| Throughput (burst) limit | 1,200 | 60-second rolling | ✅ x-ratelimit-limit / remaining |
| Daily call quota | 20 | Per calendar day | ❌ Not in headers; enforced at account layer |

In practice, the 20/day daily quota is the binding constraint on the free tier. You
will exhaust the daily quota (20 calls) long before hitting the throughput limit
(1,200/minute). The throughput limit is relevant only if all 20 calls are fired in
rapid succession.

**Practical implication for the test phase:** 15 tickers × 1 per-ticker EOD call = 15
calls/day. This fits within the 20/day quota with 5 calls of headroom for overhead
(symbol-list refresh, fundamentals queries, etc.). The 2/minute per-minute sub-limit
noted in earlier planning is superseded — the actual per-minute limit is 1,200 (not 2).

---

### Open decisions resolved by Probe 2

From `spec/phase-c-plan.md` §7:

| # | Decision | Update |
|---|----------|--------|
| 4 | Per-ticker EOD response shape | **Resolved.** 7 fields: `date` (YYYY-MM-DD string), `open`, `high`, `low`, `close`, `adjusted_close` (float), `volume` (integer). Ascending date order. No ticker/exchange in row. |
| 5 | Does the symbol-list return MarketCapitalization and Sector? | **Resolved: NO.** 7 fields only: `Code`, `Name`, `Country`, `Exchange`, `Currency`, `Type`, `Isin`. Market cap and sector must come from the fundamentals endpoint or an external source. |
| 7 | Rate-limit bucket size and window | **Resolved.** 1,200 per 60-second rolling window (throughput cap). Separate 20-call/day billing quota (not in headers). Both apply. |

---

### Plan revisions required before client build

1. **Rename `adj_close` → `adjusted_close` in the EODHD client mapping.** The field from
   the API is `adjusted_close`; the DuckDB column is `adj_close`. The client must
   explicitly rename during ingestion.

2. **Remove the 2/minute rate-limit assumption from the client.** The throughput limit is
   1,200/minute, not 2. The client's rate limiter should enforce the 20/day quota, not
   a per-minute delay. A simple daily counter (persisted in the run log) is sufficient.

3. **Universe loader market cap / sector filtering strategy for the test phase.**
   The symbol list does not provide market cap or sector. For the narrow 15-ticker test
   universe, use a manually curated list. For the production `us`/`global` scopes,
   resolve whether to call the fundamentals endpoint per ticker (budget cost: 1 call/ticker)
   or source metadata externally.

4. **Symbol list as universe seed.** The symbol list can serve as the raw ticker catalog
   (filtering by `Exchange` to remove PINK sheet tickers if desired), but liquidity
   filtering (`min_market_cap`, `min_avg_daily_value`) requires supplementary data not
   available from this endpoint.

5. **`x-ratelimit-remaining` is not a reliable daily-quota tracker.** Do not use it to
   gate the daily run. Maintain the daily call count in `tbl_run_log` instead.
