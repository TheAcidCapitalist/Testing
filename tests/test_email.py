"""Tests for src/scanner/report/email.py.

All tests mock the transport — the live Resend API is never hit.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from scanner.report.email import ResendTransport, SendError, SendResult, send_report

# ── Fake transport ───────────────────────────────────────────────────────────


@dataclass
class FakeTransport:
    """Records the last send call for inspection."""

    calls: list[dict] | None = None
    error: Exception | None = None

    def __post_init__(self) -> None:
        if self.calls is None:
            self.calls = []

    def send(
        self,
        *,
        from_addr: str,
        to: list[str],
        subject: str,
        html: str,
        attachments: list[dict],
    ) -> SendResult:
        if self.error:
            raise self.error
        call = {
            "from_addr": from_addr,
            "to": to,
            "subject": subject,
            "html": html,
            "attachments": attachments,
        }
        self.calls.append(call)
        return SendResult(id="fake-id-123")


def _make_excel(tmp_path: Path) -> Path:
    """Create a minimal fake .xlsx file."""
    p = tmp_path / "report.xlsx"
    p.write_bytes(b"PK\x03\x04fake-xlsx-content")
    return p


# ── Tests ────────────────────────────────────────────────────────────────────


class TestSuccessPath:
    def test_sends_with_body_and_attachment(self, tmp_path: Path) -> None:
        transport = FakeTransport()
        excel = _make_excel(tmp_path)
        result = send_report(
            briefing="Today's scan found 3 buys.",
            excel_path=excel,
            recipients=["team@example.com"],
            transport=transport,
        )
        assert result.id == "fake-id-123"
        assert len(transport.calls) == 1
        call = transport.calls[0]
        assert call["to"] == ["team@example.com"]
        assert "Today's scan found 3 buys." in call["html"]

    def test_attachment_is_base64_encoded(self, tmp_path: Path) -> None:
        transport = FakeTransport()
        excel = _make_excel(tmp_path)
        send_report(
            briefing="Summary.",
            excel_path=excel,
            recipients=["a@b.com"],
            transport=transport,
        )
        att = transport.calls[0]["attachments"][0]
        assert att["filename"] == "report.xlsx"
        decoded = base64.b64decode(att["content"])
        assert decoded == excel.read_bytes()

    def test_multiple_recipients(self, tmp_path: Path) -> None:
        transport = FakeTransport()
        excel = _make_excel(tmp_path)
        send_report(
            briefing="Summary.",
            excel_path=excel,
            recipients=["a@b.com", "c@d.com"],
            transport=transport,
        )
        assert transport.calls[0]["to"] == ["a@b.com", "c@d.com"]

    def test_custom_subject(self, tmp_path: Path) -> None:
        transport = FakeTransport()
        excel = _make_excel(tmp_path)
        send_report(
            briefing="Summary.",
            excel_path=excel,
            recipients=["a@b.com"],
            subject="Custom Subject",
            transport=transport,
        )
        assert transport.calls[0]["subject"] == "Custom Subject"

    def test_dashboard_url_in_body(self, tmp_path: Path) -> None:
        transport = FakeTransport()
        excel = _make_excel(tmp_path)
        send_report(
            briefing="Summary.",
            excel_path=excel,
            recipients=["a@b.com"],
            dashboard_url="https://dash.example.com",
            transport=transport,
        )
        assert "https://dash.example.com" in transport.calls[0]["html"]

    def test_returns_send_result(self, tmp_path: Path) -> None:
        transport = FakeTransport()
        excel = _make_excel(tmp_path)
        result = send_report(
            briefing="Summary.",
            excel_path=excel,
            recipients=["a@b.com"],
            transport=transport,
        )
        assert isinstance(result, SendResult)
        assert result.id == "fake-id-123"


class TestNoneBriefing:
    def test_none_briefing_still_sends(self, tmp_path: Path) -> None:
        transport = FakeTransport()
        excel = _make_excel(tmp_path)
        result = send_report(
            briefing=None,
            excel_path=excel,
            recipients=["a@b.com"],
            transport=transport,
        )
        assert result.id == "fake-id-123"
        assert len(transport.calls) == 1

    def test_none_briefing_body_is_valid_html(self, tmp_path: Path) -> None:
        transport = FakeTransport()
        excel = _make_excel(tmp_path)
        send_report(
            briefing=None,
            excel_path=excel,
            recipients=["a@b.com"],
            transport=transport,
        )
        html = transport.calls[0]["html"]
        # Should contain a graceful note, not an error dump
        assert "no ai briefing" in html.lower() or "not available" in html.lower()
        # Should NOT contain 'None' as literal text
        assert "None" not in html

    def test_none_briefing_still_has_attachment(self, tmp_path: Path) -> None:
        transport = FakeTransport()
        excel = _make_excel(tmp_path)
        send_report(
            briefing=None,
            excel_path=excel,
            recipients=["a@b.com"],
            transport=transport,
        )
        assert len(transport.calls[0]["attachments"]) == 1


class TestTransportError:
    def test_transport_error_raises_send_error(self, tmp_path: Path) -> None:
        transport = FakeTransport(error=SendError("Resend API error 500"))
        excel = _make_excel(tmp_path)
        with pytest.raises(SendError, match="500"):
            send_report(
                briefing="Summary.",
                excel_path=excel,
                recipients=["a@b.com"],
                transport=transport,
            )

    def test_transport_connection_error_propagates(self, tmp_path: Path) -> None:
        transport = FakeTransport(error=ConnectionError("network down"))
        excel = _make_excel(tmp_path)
        with pytest.raises(ConnectionError):
            send_report(
                briefing="Summary.",
                excel_path=excel,
                recipients=["a@b.com"],
                transport=transport,
            )


class TestValidation:
    def test_no_recipients_raises(self, tmp_path: Path) -> None:
        transport = FakeTransport()
        excel = _make_excel(tmp_path)
        with pytest.raises(ValueError, match="[Rr]ecipient"):
            send_report(
                briefing="Summary.",
                excel_path=excel,
                recipients=[],
                transport=transport,
            )

    def test_missing_excel_raises(self, tmp_path: Path) -> None:
        transport = FakeTransport()
        with pytest.raises(ValueError, match="not found"):
            send_report(
                briefing="Summary.",
                excel_path=tmp_path / "nonexistent.xlsx",
                recipients=["a@b.com"],
                transport=transport,
            )


class TestAttachmentIncluded:
    def test_attachment_present_in_send_call(self, tmp_path: Path) -> None:
        """The Excel attachment must actually be passed to the transport."""
        transport = FakeTransport()
        excel = _make_excel(tmp_path)
        send_report(
            briefing="Summary.",
            excel_path=excel,
            recipients=["a@b.com"],
            transport=transport,
        )
        attachments = transport.calls[0]["attachments"]
        assert len(attachments) == 1
        assert attachments[0]["filename"] == "report.xlsx"
        assert len(attachments[0]["content"]) > 0  # non-empty base64


# ── ResendTransport Tests ────────────────────────────────────────────────────


class TestResendTransport:
    def test_missing_api_key_raises(self) -> None:
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {}, clear=True):
            # If the real env has it, it's cleared above.
            # We also explicitly avoid python-dotenv reloading it by not calling load_dotenv.
            with pytest.raises(SendError, match="RESEND_API_KEY is not set"):
                t = ResendTransport(api_key="")
                t.send(
                    from_addr="a@b.com",
                    to=["c@d.com"],
                    subject="Test",
                    html="<p>test</p>",
                    attachments=[],
                )

    @pytest.fixture
    def transport(self) -> ResendTransport:
        return ResendTransport(api_key="test-key")

    def test_success_payload_shape(self, transport: ResendTransport) -> None:
        
        class MockResponse:
            status_code = 200
            def json(self):
                return {"id": "resend-msg-123"}
                
        with patch("httpx.post", return_value=MockResponse()) as mock_post:
            result = transport.send(
                from_addr="from@test.com",
                to=["to@test.com"],
                subject="Test Subject",
                html="<p>Hello</p>",
                attachments=[{"filename": "a.txt", "content": "base64"}],
            )
            
        assert result.id == "resend-msg-123"
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        
        assert kwargs["headers"]["Authorization"] == "Bearer test-key"
        assert kwargs["headers"]["Content-Type"] == "application/json"
        
        payload = kwargs["json"]
        assert payload["from"] == "from@test.com"
        assert payload["to"] == ["to@test.com"]
        assert payload["subject"] == "Test Subject"
        assert payload["html"] == "<p>Hello</p>"
        assert payload["attachments"] == [{"filename": "a.txt", "content": "base64"}]

    def test_http_400_raises_send_error(self, transport: ResendTransport) -> None:
        
        class MockResponse:
            status_code = 400
            text = "Bad Request"
            
        with patch("httpx.post", return_value=MockResponse()):
            with pytest.raises(SendError, match="Resend API error 400: Bad Request"):
                transport.send(
                    from_addr="a@b.com",
                    to=["c@d.com"],
                    subject="Test",
                    html="<p>test</p>",
                    attachments=[],
                )

    def test_network_error_raises_send_error(self, transport: ResendTransport) -> None:
        
        with patch("httpx.post", side_effect=httpx.ConnectError("network down")):
            with pytest.raises(SendError, match="HTTP error sending email: network down"):
                transport.send(
                    from_addr="a@b.com",
                    to=["c@d.com"],
                    subject="Test",
                    html="<p>test</p>",
                    attachments=[],
                )
