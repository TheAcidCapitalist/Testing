"""Tests for src/scanner/data/eodhd.py.

All HTTP is mocked via unittest.mock — no real network calls are made.
Storage interactions use a real in-memory DuckDB instance so the budget-counter
behaviour is exercised end-to-end through actual SQL rather than mocked away.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import httpx
import pandas as pd
import pytest

from scanner.data.eodhd import (
    CallBudget,
    DailyBudgetExceeded,
    EODHDAuthError,
    EODHDClient,
    EODHDError,
    EODHDForbiddenError,
    EODHDNotFoundError,
    EODHDServerError,
    EODHDThrottleError,
)
from scanner.data.storage import Storage

# ── Constants ─────────────────────────────────────────────────────────────────

_RUN_ID = "run-test-001"
_RUN_DATE = date(2026, 5, 19)
_DAILY_LIMIT = 20

# Two bars in EODHD response format (adjusted_close, not adj_close).
_SAMPLE_BARS = [
    {
        "date": "2026-05-01",
        "open": 200.0,
        "high": 205.0,
        "low": 198.0,
        "close": 203.0,
        "adjusted_close": 202.5,
        "volume": 10_000_000,
    },
    {
        "date": "2026-05-02",
        "open": 203.0,
        "high": 210.0,
        "low": 202.0,
        "close": 208.0,
        "adjusted_close": 207.5,
        "volume": 12_000_000,
    },
]


# ── Mock-response helpers ─────────────────────────────────────────────────────


def _ok(data: list | None = None) -> MagicMock:
    """Fake 200 OK httpx response."""
    m = MagicMock()
    m.status_code = 200
    m.json.return_value = data if data is not None else _SAMPLE_BARS
    return m


def _err(status_code: int) -> MagicMock:
    """Fake error httpx response."""
    m = MagicMock()
    m.status_code = status_code
    m.raise_for_status.side_effect = httpx.HTTPStatusError(
        message=f"HTTP {status_code}",
        request=MagicMock(),
        response=m,
    )
    return m


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def storage():
    """In-memory DuckDB with a pre-initialised run-log row."""
    with Storage(":memory:") as s:
        s.log_run_start(_RUN_ID, _RUN_DATE, "sample")
        yield s


@pytest.fixture()
def budget(storage):
    return CallBudget(storage, _RUN_ID, daily_limit=_DAILY_LIMIT)


@pytest.fixture()
def client(budget):
    return EODHDClient(budget, api_key="testkey")


# ── CallBudget ────────────────────────────────────────────────────────────────


class TestCallBudget:
    def test_initial_used_is_zero(self, storage):
        b = CallBudget(storage, _RUN_ID, daily_limit=20)
        assert b.used == 0

    def test_initial_remaining_equals_limit(self, storage):
        b = CallBudget(storage, _RUN_ID, daily_limit=20)
        assert b.remaining == 20

    def test_charge_increments_used(self, storage):
        b = CallBudget(storage, _RUN_ID, daily_limit=20)
        b.charge()
        assert b.used == 1
        assert b.remaining == 19

    def test_charge_persists_to_storage(self, storage):
        b = CallBudget(storage, _RUN_ID, daily_limit=20)
        b.charge()
        assert storage.get_api_calls_used(_RUN_ID) == 1

    def test_multiple_charges_accumulate(self, storage):
        b = CallBudget(storage, _RUN_ID, daily_limit=20)
        b.charge()
        b.charge()
        b.charge()
        assert b.used == 3
        assert storage.get_api_calls_used(_RUN_ID) == 3

    def test_charge_raises_at_limit(self, storage):
        b = CallBudget(storage, _RUN_ID, daily_limit=3)
        b.charge()
        b.charge()
        b.charge()
        with pytest.raises(DailyBudgetExceeded):
            b.charge()

    def test_counter_unchanged_when_limit_exceeded(self, storage):
        b = CallBudget(storage, _RUN_ID, daily_limit=2)
        b.charge()
        b.charge()
        with pytest.raises(DailyBudgetExceeded):
            b.charge()
        # Both in-memory and storage must remain at 2 (not 3).
        assert b.used == 2
        assert storage.get_api_calls_used(_RUN_ID) == 2

    def test_loads_existing_counter_from_storage(self, storage):
        """A same-day re-run continues from the stored count."""
        storage.update_api_calls_used(_RUN_ID, 15)
        b = CallBudget(storage, _RUN_ID, daily_limit=20)
        assert b.used == 15
        assert b.remaining == 5

    def test_unknown_run_id_starts_at_zero(self, storage):
        b = CallBudget(storage, "nonexistent-run", daily_limit=20)
        assert b.used == 0

    def test_daily_budget_exceeded_message_contains_limit(self, storage):
        b = CallBudget(storage, _RUN_ID, daily_limit=1)
        b.charge()
        with pytest.raises(DailyBudgetExceeded, match="1"):
            b.charge()


# ── EODHDClient — happy path ──────────────────────────────────────────────────


class TestFetchEodHappyPath:
    def test_returns_dataframe(self, client):
        with patch("httpx.get", return_value=_ok()):
            df = client.fetch_eod("AAPL.US")
        assert isinstance(df, pd.DataFrame)

    def test_canonical_columns_exact(self, client):
        with patch("httpx.get", return_value=_ok()):
            df = client.fetch_eod("AAPL.US")
        assert list(df.columns) == ["date", "open", "high", "low", "close", "adj_close", "volume"]

    def test_adjusted_close_renamed_to_adj_close(self, client):
        """EODHD field 'adjusted_close' must become 'adj_close' in output."""
        with patch("httpx.get", return_value=_ok()):
            df = client.fetch_eod("AAPL.US")
        assert "adj_close" in df.columns
        assert "adjusted_close" not in df.columns

    def test_sample_values_intact(self, client):
        with patch("httpx.get", return_value=_ok()):
            df = client.fetch_eod("AAPL.US")
        row0 = df.iloc[0]
        assert row0["open"] == pytest.approx(200.0)
        assert row0["close"] == pytest.approx(203.0)
        assert row0["adj_close"] == pytest.approx(202.5)
        assert row0["volume"] == pytest.approx(10_000_000)

    def test_rows_ascending_by_date(self, client):
        """Output is sorted ascending by date regardless of response order."""
        reversed_bars = list(reversed(_SAMPLE_BARS))
        with patch("httpx.get", return_value=_ok(data=reversed_bars)):
            df = client.fetch_eod("AAPL.US")
        dates = df["date"].tolist()
        assert dates == sorted(dates)

    def test_date_column_is_datetime64(self, client):
        with patch("httpx.get", return_value=_ok()):
            df = client.fetch_eod("AAPL.US")
        assert pd.api.types.is_datetime64_any_dtype(df["date"])

    def test_numeric_ohlcv_columns(self, client):
        with patch("httpx.get", return_value=_ok()):
            df = client.fetch_eod("AAPL.US")
        for col in ("open", "high", "low", "close", "adj_close", "volume"):
            assert pd.api.types.is_numeric_dtype(df[col]), f"{col} not numeric"

    def test_row_count_matches_response(self, client):
        with patch("httpx.get", return_value=_ok()):
            df = client.fetch_eod("AAPL.US")
        assert len(df) == len(_SAMPLE_BARS)


# ── Budget enforcement ────────────────────────────────────────────────────────


class TestBudgetEnforcement:
    def test_budget_exceeded_raises_before_http_call(self, storage):
        """When budget is exhausted, no HTTP request must be made."""
        storage.update_api_calls_used(_RUN_ID, _DAILY_LIMIT)
        b = CallBudget(storage, _RUN_ID, daily_limit=_DAILY_LIMIT)
        c = EODHDClient(b, api_key="testkey")

        with patch("httpx.get") as mock_get:
            with pytest.raises(DailyBudgetExceeded):
                c.fetch_eod("AAPL.US")
            mock_get.assert_not_called()

    def test_counter_incremented_once_after_success(self, storage):
        """A successful call increments the storage counter by exactly 1."""
        b = CallBudget(storage, _RUN_ID, daily_limit=_DAILY_LIMIT)
        c = EODHDClient(b, api_key="testkey")

        with patch("httpx.get", return_value=_ok()):
            c.fetch_eod("AAPL.US")

        assert storage.get_api_calls_used(_RUN_ID) == 1

    def test_counter_incremented_on_404(self, storage):
        """Budget is charged for a 404 since the server processed it (billable)."""
        b = CallBudget(storage, _RUN_ID, daily_limit=_DAILY_LIMIT)
        c = EODHDClient(b, api_key="testkey")

        with patch("httpx.get", return_value=_err(404)):
            with pytest.raises(EODHDNotFoundError):
                c.fetch_eod("AAPL.US")

        assert storage.get_api_calls_used(_RUN_ID) == 1

    @pytest.mark.parametrize("status_code, expected_exc", [
        (401, EODHDAuthError),
        (403, EODHDForbiddenError),
        (423, EODHDForbiddenError),
        (429, EODHDThrottleError),
        (500, EODHDServerError),
        (503, EODHDServerError),
    ])
    def test_counter_not_incremented_on_unbilled_errors(self, storage, status_code, expected_exc):
        """Budget is NOT charged for auth, permission, throttle, or server faults."""
        b = CallBudget(storage, _RUN_ID, daily_limit=_DAILY_LIMIT)
        c = EODHDClient(b, api_key="testkey")

        with patch("httpx.get", return_value=_err(status_code)):
            with pytest.raises(expected_exc):
                c.fetch_eod("AAPL.US")

        assert storage.get_api_calls_used(_RUN_ID) == 0

    def test_budget_state_persists_across_reinit(self, storage):
        """A new CallBudget for the same run_id picks up the previous count."""
        b1 = CallBudget(storage, _RUN_ID, daily_limit=_DAILY_LIMIT)
        c = EODHDClient(b1, api_key="testkey")
        with patch("httpx.get", return_value=_ok()):
            c.fetch_eod("AAPL.US")

        # Simulate process restart / re-run.
        b2 = CallBudget(storage, _RUN_ID, daily_limit=_DAILY_LIMIT)
        assert b2.used == 1

    def test_two_successful_calls_increment_by_two(self, storage):
        b = CallBudget(storage, _RUN_ID, daily_limit=_DAILY_LIMIT)
        c = EODHDClient(b, api_key="testkey")

        with patch("httpx.get", return_value=_ok()):
            c.fetch_eod("AAPL.US")
            c.fetch_eod("MSFT.US")

        assert storage.get_api_calls_used(_RUN_ID) == 2


# ── Error response handling ───────────────────────────────────────────────────


class TestErrorResponses:
    def test_404_raises_not_found(self, client):
        with patch("httpx.get", return_value=_err(404)):
            with pytest.raises(EODHDNotFoundError):
                client.fetch_eod("ZZZZ.US")

    def test_401_raises_auth_error(self, client):
        with patch("httpx.get", return_value=_err(401)):
            with pytest.raises(EODHDAuthError):
                client.fetch_eod("AAPL.US")

    def test_403_raises_forbidden(self, client):
        with patch("httpx.get", return_value=_err(403)):
            with pytest.raises(EODHDForbiddenError):
                client.fetch_eod("AAPL.US")

    def test_423_raises_forbidden(self, client):
        """423 (Locked) is the bulk-EOD paywall status — must raise Forbidden."""
        with patch("httpx.get", return_value=_err(423)):
            with pytest.raises(EODHDForbiddenError):
                client.fetch_eod("AAPL.US")

    def test_429_raises_throttle(self, client):
        with patch("httpx.get", return_value=_err(429)):
            with pytest.raises(EODHDThrottleError):
                client.fetch_eod("AAPL.US")

    def test_500_raises_server_error(self, client):
        with patch("httpx.get", return_value=_err(500)):
            with pytest.raises(EODHDServerError):
                client.fetch_eod("AAPL.US")

    def test_503_raises_server_error(self, client):
        with patch("httpx.get", return_value=_err(503)):
            with pytest.raises(EODHDServerError):
                client.fetch_eod("AAPL.US")

    def test_empty_array_raises_not_found(self, client):
        with patch("httpx.get", return_value=_ok(data=[])):
            with pytest.raises(EODHDNotFoundError):
                client.fetch_eod("AAPL.US")

    def test_network_error_raises_eodhd_error(self, storage):
        b = CallBudget(storage, _RUN_ID, daily_limit=_DAILY_LIMIT)
        c = EODHDClient(b, api_key="testkey")

        with patch("httpx.get", side_effect=httpx.ConnectError("connection refused")):
            with pytest.raises(EODHDError):
                c.fetch_eod("AAPL.US")

        assert storage.get_api_calls_used(_RUN_ID) == 0

    def test_error_types_are_eodhd_error_subclasses(self):
        assert issubclass(DailyBudgetExceeded, EODHDError)
        assert issubclass(EODHDAuthError, EODHDError)
        assert issubclass(EODHDForbiddenError, EODHDError)
        assert issubclass(EODHDNotFoundError, EODHDError)
        assert issubclass(EODHDThrottleError, EODHDError)
        assert issubclass(EODHDServerError, EODHDError)


# ── Bulk-EOD flag ─────────────────────────────────────────────────────────────


class TestBulkEodFlag:
    def test_use_bulk_eod_raises_not_implemented(self, budget):
        c = EODHDClient(budget, api_key="testkey", use_bulk_eod=True)
        with pytest.raises(NotImplementedError, match="bulk EOD not available on free tier"):
            c.fetch_eod("AAPL.US")

    def test_use_bulk_eod_does_not_consume_budget(self, storage, budget):
        """NotImplementedError is raised before charge() — no budget consumed."""
        c = EODHDClient(budget, api_key="testkey", use_bulk_eod=True)
        with pytest.raises(NotImplementedError):
            c.fetch_eod("AAPL.US")
        assert storage.get_api_calls_used(_RUN_ID) == 0

    def test_default_use_bulk_eod_is_false(self, budget):
        c = EODHDClient(budget, api_key="testkey")
        assert c._use_bulk_eod is False


# ── Request parameters ────────────────────────────────────────────────────────


class TestRequestParams:
    def test_start_date_string_in_params(self, client):
        with patch("httpx.get", return_value=_ok()) as mock_get:
            client.fetch_eod("AAPL.US", start_date="2025-01-01")
        params = mock_get.call_args.kwargs["params"]
        assert params["from"] == "2025-01-01"

    def test_start_date_date_object_in_params(self, client):
        with patch("httpx.get", return_value=_ok()) as mock_get:
            client.fetch_eod("AAPL.US", start_date=date(2025, 1, 1))
        params = mock_get.call_args.kwargs["params"]
        assert params["from"] == "2025-01-01"

    def test_end_date_in_params(self, client):
        with patch("httpx.get", return_value=_ok()) as mock_get:
            client.fetch_eod("AAPL.US", end_date=date(2026, 5, 18))
        params = mock_get.call_args.kwargs["params"]
        assert params["to"] == "2026-05-18"

    def test_no_dates_omits_from_to_params(self, client):
        with patch("httpx.get", return_value=_ok()) as mock_get:
            client.fetch_eod("AAPL.US")
        params = mock_get.call_args.kwargs["params"]
        assert "from" not in params
        assert "to" not in params

    def test_period_is_daily(self, client):
        with patch("httpx.get", return_value=_ok()) as mock_get:
            client.fetch_eod("AAPL.US")
        params = mock_get.call_args.kwargs["params"]
        assert params["period"] == "d"

    def test_format_is_json(self, client):
        with patch("httpx.get", return_value=_ok()) as mock_get:
            client.fetch_eod("AAPL.US")
        params = mock_get.call_args.kwargs["params"]
        assert params["fmt"] == "json"


# ── Miscellaneous ─────────────────────────────────────────────────────────────


class TestMiscellaneous:
    def test_no_api_key_raises_auth_error(self, budget, monkeypatch):
        monkeypatch.delenv("EODHD_API_KEY", raising=False)
        with pytest.raises(EODHDAuthError):
            EODHDClient(budget, api_key="")

    def test_explicit_key_takes_precedence_over_env(self, storage, monkeypatch):
        monkeypatch.setenv("EODHD_API_KEY", "envkey")
        b = CallBudget(storage, _RUN_ID, daily_limit=_DAILY_LIMIT)
        c = EODHDClient(b, api_key="explicit-key")
        with patch("httpx.get", return_value=_ok()) as mock_get:
            c.fetch_eod("AAPL.US")
        params = mock_get.call_args.kwargs["params"]
        assert params["api_token"] == "explicit-key"

    def test_api_token_included_in_every_request(self, client):
        with patch("httpx.get", return_value=_ok()) as mock_get:
            client.fetch_eod("AAPL.US")
        params = mock_get.call_args.kwargs["params"]
        assert "api_token" in params

    def test_ticker_appears_in_url(self, client):
        with patch("httpx.get", return_value=_ok()) as mock_get:
            client.fetch_eod("VOD.LSE")
        url = mock_get.call_args.args[0]
        assert "VOD.LSE" in url
