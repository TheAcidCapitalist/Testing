"""EODHD API client for signal-scanner.

Per-ticker EOD endpoint only — the bulk-EOD endpoint is paywalled on the free
tier (HTTP 423, confirmed in Probe 1, 2026-05-16).  The per-ticker endpoint is
confirmed available on the free tier (Probe 2, 2026-05-18).

Daily-budget enforcement is the only rate-limit mechanism in use.  The per-minute
throughput cap (1,200 calls / 60-second rolling window, confirmed in Probe 2) is not
a binding constraint at ≤ 20 calls/day and no per-request sleep is needed.

The ``use_bulk_eod`` flag exists so the paid-tier upgrade is a configuration change
rather than a code rewrite.  Set it to ``True`` and implement the bulk path when an
upgraded plan is available.

See spec/eodhd-probe-notes.md for confirmed response shapes and rate-limit data.
See spec/phase-c-plan.md §8 Session 2 for the design rationale.
"""

from __future__ import annotations

import os
from datetime import date
from typing import TYPE_CHECKING

import httpx
import pandas as pd
from dotenv import load_dotenv

if TYPE_CHECKING:
    from scanner.data.storage import Storage

# Load .env once at import time so that os.environ is populated for the lifetime
# of the process.  If .env does not exist this is a no-op.
load_dotenv()

_BASE_URL = "https://eodhd.com/api"
_CANONICAL_COLUMNS = ["date", "open", "high", "low", "close", "adj_close", "volume"]


# ── Exceptions ────────────────────────────────────────────────────────────────


class EODHDError(Exception):
    """Base class for all EODHD client errors."""


class DailyBudgetExceeded(EODHDError):
    """Raised *before* issuing a call that would exceed the daily quota.

    The HTTP request is never made when this exception is raised.
    """


class EODHDAuthError(EODHDError):
    """401 — invalid or missing API key."""


class EODHDForbiddenError(EODHDError):
    """403 / 423 — endpoint is paywalled or access is denied on this plan tier."""


class EODHDNotFoundError(EODHDError):
    """404 — no data found for the requested ticker, or empty response array."""


class EODHDThrottleError(EODHDError):
    """429 — per-minute throughput cap hit; treat as transient.

    At ≤ 20 calls/day this should never occur.  The orchestrator decides
    whether to retry — the client does not retry internally.
    """


class EODHDServerError(EODHDError):
    """5xx — server-side error; treat as transient."""


# ── CallBudget ────────────────────────────────────────────────────────────────


class CallBudget:
    """Daily API call counter backed by ``tbl_run_log.api_calls_used`` in Storage.

    Initialised from the stored counter so that a same-day re-run picks up
    where the previous run left off (the counter does not reset).

    Every :meth:`charge` call checks the budget *before* the API request is
    issued.  If the budget would be exceeded, :exc:`DailyBudgetExceeded` is
    raised and no HTTP request is made.

    Parameters
    ----------
    storage:
        The active :class:`~scanner.data.storage.Storage` instance.
        ``log_run_start`` must have been called for ``run_id`` before this
        object is constructed, otherwise persistence is a silent no-op.
    run_id:
        The run identifier used as the primary key in ``tbl_run_log``.
    daily_limit:
        Maximum API calls per calendar day.  Default 20 (EODHD free tier).
    """

    def __init__(
        self,
        storage: Storage,
        run_id: str,
        *,
        daily_limit: int = 20,
    ) -> None:
        self._storage = storage
        self._run_id = run_id
        self._daily_limit = daily_limit
        # Read from storage so a same-day re-run continues from the last value.
        self._used: int = storage.get_api_calls_used(run_id)

    # ── Public interface ──────────────────────────────────────────────────────

    def charge(self, n: int = 1) -> None:
        """Reserve *n* calls from the daily budget.

        The budget is checked *before* incrementing.  If ``used + n`` would
        exceed ``daily_limit``, :exc:`DailyBudgetExceeded` is raised without
        modifying the counter.  On success the counter is incremented in memory
        and persisted to storage immediately.
        """
        if self._used + n > self._daily_limit:
            raise DailyBudgetExceeded(
                f"Daily API call budget of {self._daily_limit} would be exceeded "
                f"({self._used} used, {n} requested)."
            )
        self._used += n
        self._storage.update_api_calls_used(self._run_id, self._used)

    @property
    def used(self) -> int:
        """Calls consumed so far today."""
        return self._used

    @property
    def remaining(self) -> int:
        """Calls remaining in today's budget."""
        return max(0, self._daily_limit - self._used)


# ── EODHDClient ───────────────────────────────────────────────────────────────


class EODHDClient:
    """EODHD API client.

    Fetches per-ticker EOD OHLCV data and returns it in the canonical shape
    expected by the scanner pipeline.  All calls are charged through the shared
    :class:`CallBudget` *before* the HTTP request is issued.

    Parameters
    ----------
    budget:
        Shared daily call budget.  Injected so tests can supply a controlled
        instance without touching global state.
    api_key:
        EODHD API key.  Falls back to the ``EODHD_API_KEY`` environment
        variable (loaded from ``.env`` at module import).
    use_bulk_eod:
        Configuration flag for the paid-tier bulk-EOD endpoint.  Defaults to
        ``False`` (free-tier operation).  When set to ``True`` the method raises
        :exc:`NotImplementedError` — the structure is in place for a future
        paid-tier implementation but the code path is an explicit placeholder,
        not a half-built implementation.  Upgrading to a paid plan means setting
        this flag and implementing the bulk path; no other changes are needed.
    """

    def __init__(
        self,
        budget: CallBudget,
        *,
        api_key: str | None = None,
        use_bulk_eod: bool = False,
    ) -> None:
        resolved_key = api_key or os.environ.get("EODHD_API_KEY", "")
        if not resolved_key:
            raise EODHDAuthError(
                "No EODHD API key provided. "
                "Set EODHD_API_KEY in .env or pass api_key= to EODHDClient."
            )
        self._api_key = resolved_key
        self._budget = budget
        self._use_bulk_eod = use_bulk_eod

    # ── Public interface ──────────────────────────────────────────────────────

    def fetch_eod(
        self,
        ticker: str,
        *,
        start_date: date | str | None = None,
        end_date: date | str | None = None,
    ) -> pd.DataFrame:
        """Fetch per-ticker EOD history from EODHD.

        Returns a DataFrame in the canonical OHLCV shape, ascending by date::

            date       datetime64[ns]   # trading date, timezone-naive
            open       float64
            high       float64
            low        float64
            close      float64          # unadjusted close
            adj_close  float64          # split/dividend-adjusted (EODHD: adjusted_close)
            volume     float64

        The EODHD field ``adjusted_close`` is renamed to ``adj_close`` here in
        the client layer — nothing downstream needs to know the original name.

        Parameters
        ----------
        ticker:
            EODHD ticker in ``'{CODE}.{EXCHANGE}'`` format, e.g. ``'AAPL.US'``.
        start_date:
            Inclusive start of the requested range.  ISO 8601 string or
            :class:`~datetime.date`.  Omit for full available history.
        end_date:
            Inclusive end of the requested range.  ISO 8601 string or
            :class:`~datetime.date`.  Omit to fetch through today.

        Raises
        ------
        DailyBudgetExceeded
            If the budget would be exceeded.  Raised *before* the HTTP call.
        NotImplementedError
            If ``use_bulk_eod=True`` (placeholder for paid-tier path).
        EODHDAuthError
            On a 401 response.
        EODHDForbiddenError
            On a 403 or 423 response.
        EODHDNotFoundError
            On a 404 response or an empty response array.
        EODHDThrottleError
            On a 429 response.
        EODHDServerError
            On a 5xx response.
        EODHDError
            On a network-level failure.
        """
        if self._use_bulk_eod:
            raise NotImplementedError(
                "bulk EOD not available on free tier; requires upgraded plan"
            )

        # ── Budget check is BEFORE the HTTP call ─────────────────────────────
        # If this raises, the HTTP request is never issued.
        self._budget.charge(1)

        params: dict[str, str] = {
            "api_token": self._api_key,
            "period": "d",
            "fmt": "json",
        }
        if start_date is not None:
            params["from"] = str(start_date)
        if end_date is not None:
            params["to"] = str(end_date)

        url = f"{_BASE_URL}/eod/{ticker}"
        try:
            response = httpx.get(url, params=params, timeout=30)
        except httpx.RequestError as exc:
            raise EODHDError(f"Network error fetching '{ticker}': {exc}") from exc

        self._handle_status(response, ticker)

        data = response.json()
        if not data:
            raise EODHDNotFoundError(
                f"EODHD returned an empty array for ticker '{ticker}'."
            )

        return self._normalise(pd.DataFrame(data))

    # ── Private helpers ───────────────────────────────────────────────────────

    def _handle_status(self, response: httpx.Response, ticker: str) -> None:
        """Raise a typed exception for any non-200 status code."""
        status = response.status_code
        if status == 200:
            return
        if status == 401:
            raise EODHDAuthError("401 Unauthorised — verify your EODHD_API_KEY.")
        if status in (403, 423):
            raise EODHDForbiddenError(
                f"{status} — endpoint is paywalled or forbidden on this plan tier. "
                "See spec/phase-c-plan.md §4 for the production sourcing options."
            )
        if status == 404:
            raise EODHDNotFoundError(f"404 — ticker '{ticker}' not found on EODHD.")
        if status == 429:
            raise EODHDThrottleError(
                "429 Too Many Requests — per-minute throughput cap hit. "
                "Treat as transient; the orchestrator decides whether to retry."
            )
        if status >= 500:
            raise EODHDServerError(
                f"{status} Server error from EODHD; treat as transient."
            )
        # Fallback for any other unexpected status.
        response.raise_for_status()

    @staticmethod
    def _normalise(df: pd.DataFrame) -> pd.DataFrame:
        """Rename EODHD fields and coerce types to the canonical OHLCV shape.

        EODHD returns ``adjusted_close``; the canonical column is ``adj_close``.
        The rename happens here — nothing downstream sees the original name.
        """
        df = df.rename(columns={"adjusted_close": "adj_close"})
        df["date"] = pd.to_datetime(df["date"])
        for col in ("open", "high", "low", "close", "adj_close", "volume"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df[_CANONICAL_COLUMNS].sort_values("date").reset_index(drop=True)
