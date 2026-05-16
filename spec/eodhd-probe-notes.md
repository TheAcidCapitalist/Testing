# EODHD API Probe Notes

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
