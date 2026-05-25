"""Tests for src/scanner/agent/briefing.py.

All tests mock the OpenAI client — the live API is never hit.
Assertions are structural (return type, fail-soft behaviour), not on
generated wording.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from scanner.agent.briefing import generate_briefing

# ── Fixtures ─────────────────────────────────────────────────────────────────


def _sample_dict(n_tickers: int = 3) -> dict:
    """Minimal canonical dashboard dict."""
    tickers = []
    for i in range(n_tickers):
        direction = ["buy", "sell", "neutral"][i % 3]
        tickers.append({
            "ticker": f"TK{i}",
            "exchange": "US",
            "date": "2024-06-15",
            "direction": direction,
            "combo_score": 0.30 + i * 0.05,
            "rank_score": 0.90 - i * 0.10,
            "agreement_count": 6,
            "n_trade_indicators": 8,
            "signals_firing": ["rsi", "stochastic"],
            "vol_confirmation": "confirm",
            "volume_confirmation": "confirm",
            "days_since_breakout": 2,
            "meta": {
                "name": f"Company {i}",
                "currency": "USD",
                "market_cap_usd": 1e12,
                "sector": "Technology",
                "region": "North America",
            },
            "mtf_alignment": {
                "resolutions_available": 2,
                "resolutions_aligned": 2,
                "alignment_fraction": 1.0,
            },
            "indicators": {},
        })
    return {
        "meta": {
            "schema_version": "1.0",
            "run_date": "2024-06-15",
            "generated_at": "2024-06-15T12:00:00+00:00",
            "scope": "sample",
            "combination_name": "default",
            "n_tickers_scored": n_tickers,
            "n_tickers_universe": 100,
            "n_buy": sum(1 for t in tickers if t["direction"] == "buy"),
            "n_sell": sum(1 for t in tickers if t["direction"] == "sell"),
            "n_neutral": sum(1 for t in tickers if t["direction"] == "neutral"),
        },
        "tickers": tickers,
    }


def _empty_dict() -> dict:
    return {
        "meta": {
            "schema_version": "1.0",
            "run_date": "2024-06-15",
            "generated_at": "2024-06-15T12:00:00+00:00",
            "scope": "sample",
            "combination_name": "default",
            "n_tickers_scored": 0,
            "n_tickers_universe": 0,
            "n_buy": 0,
            "n_sell": 0,
            "n_neutral": 0,
        },
        "tickers": [],
    }


def _mock_client(response_text: str = "Today's scan summary.") -> MagicMock:
    """Build a mock OpenAI client that returns a canned text response."""
    message = SimpleNamespace(content=response_text)
    choice = SimpleNamespace(message=message)
    response = SimpleNamespace(choices=[choice])
    client = MagicMock()
    client.chat.completions.create.return_value = response
    return client


# ── Tests ────────────────────────────────────────────────────────────────────


class TestHappyPath:
    def test_returns_model_text(self) -> None:
        client = _mock_client("Daily briefing: 3 tickers scanned.")
        result = generate_briefing(_sample_dict(), client=client)
        assert result == "Daily briefing: 3 tickers scanned."

    def test_client_called_with_model(self) -> None:
        client = _mock_client()
        generate_briefing(_sample_dict(), client=client, model="custom-model")
        call_kwargs = client.chat.completions.create.call_args
        assert call_kwargs.kwargs["model"] == "custom-model"

    def test_non_neutral_only_in_payload(self) -> None:
        """The user message should not contain neutral tickers."""
        client = _mock_client()
        data = _sample_dict(3)  # TK0=buy, TK1=sell, TK2=neutral
        generate_briefing(data, client=client)
        messages = client.chat.completions.create.call_args.kwargs["messages"]
        user_msg = next(m["content"] for m in messages if m["role"] == "user")
        assert "TK0" in user_msg
        assert "TK1" in user_msg
        assert "TK2" not in user_msg


class TestMissingApiKey:
    def test_no_key_returns_none(self) -> None:
        """When no client is passed and OPENAI_API_KEY is unset, returns None."""
        with patch.dict("os.environ", {}, clear=True):
            # Also remove the key if it exists in the real env
            import os
            env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
            with patch.dict("os.environ", env, clear=True):
                result = generate_briefing(_sample_dict())
                assert result is None


class TestClientRaises:
    def test_network_error_returns_none(self) -> None:
        client = MagicMock()
        client.chat.completions.create.side_effect = ConnectionError("network down")
        result = generate_briefing(_sample_dict(), client=client)
        assert result is None

    def test_api_error_returns_none(self) -> None:
        client = MagicMock()
        client.chat.completions.create.side_effect = RuntimeError("API overloaded")
        result = generate_briefing(_sample_dict(), client=client)
        assert result is None

    def test_malformed_response_returns_none(self) -> None:
        """Response with no text blocks returns None."""
        response = SimpleNamespace(choices=[])
        client = MagicMock()
        client.chat.completions.create.return_value = response
        result = generate_briefing(_sample_dict(), client=client)
        assert result is None

    def test_none_content_returns_none(self) -> None:
        """Response with None content attribute returns None."""
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None))])
        client = MagicMock()
        client.chat.completions.create.return_value = response
        result = generate_briefing(_sample_dict(), client=client)
        assert result is None


class TestEmptyRun:
    def test_empty_dict_no_exception(self) -> None:
        client = _mock_client("No tickers scored today.")
        result = generate_briefing(_empty_dict(), client=client)
        assert result == "No tickers scored today."

    def test_empty_dict_client_still_called(self) -> None:
        client = _mock_client()
        generate_briefing(_empty_dict(), client=client)
        assert client.chat.completions.create.called


class TestMalformedInput:
    def test_none_data_returns_none(self) -> None:
        client = _mock_client()
        result = generate_briefing(None, client=client)
        assert result is None

    def test_missing_meta_key(self) -> None:
        client = _mock_client()
        result = generate_briefing({"tickers": []}, client=client)
        # Should still work — meta defaults to empty dict
        assert isinstance(result, str)

    def test_missing_tickers_key(self) -> None:
        client = _mock_client()
        result = generate_briefing({"meta": {}}, client=client)
        assert isinstance(result, str)


class TestWhitespaceHandling:
    def test_whitespace_only_response_returns_none(self) -> None:
        client = _mock_client("   \n  ")
        result = generate_briefing(_sample_dict(), client=client)
        assert result is None
