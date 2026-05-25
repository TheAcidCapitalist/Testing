"""Daily report email sender via Resend REST API.

Assembles the email body from pre-built pieces (briefing text, Excel path,
dashboard URL) and sends via the Resend API using ``httpx``.  Does **not**
build the dict, Excel, or briefing itself — the D5 orchestrator wires those.

Two behaviours that differ from the briefing layer:

1. **None briefing is normal.**  The body renders gracefully without it.
2. **Send failure is real failure.**  Unlike the briefing's fail-soft, a
   Resend error means the report didn't go out — we raise ``SendError``
   so the orchestrator can detect non-delivery.
"""

from __future__ import annotations

import base64
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)

# ── Public types ─────────────────────────────────────────────────────────────


class SendError(Exception):
    """Raised when the email could not be delivered."""


@dataclass(frozen=True)
class SendResult:
    """Result of a successful send."""
    id: str
    """Resend message ID."""


class Transport(Protocol):
    """Pluggable email transport — the only method the sender calls."""

    def send(
        self,
        *,
        from_addr: str,
        to: list[str],
        subject: str,
        html: str,
        attachments: list[dict],
    ) -> SendResult: ...


# ── Resend transport (default) ───────────────────────────────────────────────


class ResendTransport:
    """Sends email through the Resend REST API using ``httpx``."""

    _URL = "https://api.resend.com/emails"

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.environ.get("RESEND_API_KEY", "")

    def send(
        self,
        *,
        from_addr: str,
        to: list[str],
        subject: str,
        html: str,
        attachments: list[dict],
    ) -> SendResult:
        import httpx

        if not self._api_key:
            raise SendError("RESEND_API_KEY is not set")

        payload: dict = {
            "from": from_addr,
            "to": to,
            "subject": subject,
            "html": html,
        }
        if attachments:
            payload["attachments"] = attachments

        try:
            resp = httpx.post(
                self._URL,
                json=payload,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                timeout=30.0,
            )
        except httpx.HTTPError as exc:
            raise SendError(f"HTTP error sending email: {exc}") from exc

        if resp.status_code >= 400:
            raise SendError(
                f"Resend API error {resp.status_code}: {resp.text}"
            )

        data = resp.json()
        return SendResult(id=data.get("id", "unknown"))


# ── Public API ───────────────────────────────────────────────────────────────

# Default from-address and recipients (overridable via params or env).
# Note: 'onboarding@resend.dev' only works in test mode (delivers only to the
# account email). Production requires a verified domain via REPORT_FROM_ADDR.
_DEFAULT_FROM = "onboarding@resend.dev"


def send_report(
    *,
    briefing: str | None,
    excel_path: str | Path,
    recipients: list[str] | None = None,
    subject: str = "Daily Signal Scanner Report",
    from_addr: str | None = None,
    dashboard_url: str | None = None,
    transport: Transport | None = None,
) -> SendResult:
    """Assemble and send the daily report email.

    Parameters
    ----------
    briefing:
        The Haiku-generated briefing text, or ``None`` (normal — the body
        renders gracefully without it).
    excel_path:
        Path to the .xlsx attachment.
    recipients:
        List of email addresses.  Falls back to ``REPORT_RECIPIENTS`` env
        var (comma-separated).
    subject:
        Email subject line.
    from_addr:
        Sender address.  Falls back to ``REPORT_FROM_ADDR`` env var.
    dashboard_url:
        Optional link to the live dashboard.
    transport:
        Pluggable transport (default: :class:`ResendTransport`).

    Returns
    -------
    SendResult
        On success.

    Raises
    ------
    SendError
        On any delivery failure — the report did not go out.
    ValueError
        If recipients are missing or the Excel file doesn't exist.
    """
    # ── Resolve defaults ─────────────────────────────────────────────────
    if recipients is None:
        env_recip = os.environ.get("REPORT_RECIPIENTS", "")
        recipients = [r.strip() for r in env_recip.split(",") if r.strip()]
    if not recipients:
        raise ValueError("No recipients specified and REPORT_RECIPIENTS is empty")

    if from_addr is None:
        from_addr = os.environ.get("REPORT_FROM_ADDR", _DEFAULT_FROM)

    excel = Path(excel_path)
    if not excel.exists():
        raise ValueError(f"Excel file not found: {excel}")

    if transport is None:
        transport = ResendTransport()

    # ── Build the email body ─────────────────────────────────────────────
    html = _build_body(briefing=briefing, dashboard_url=dashboard_url)

    # ── Prepare the attachment ───────────────────────────────────────────
    attachment = _encode_attachment(excel)

    # ── Send ─────────────────────────────────────────────────────────────
    log.info("Sending report to %s", recipients)
    result = transport.send(
        from_addr=from_addr,
        to=recipients,
        subject=subject,
        html=html,
        attachments=[attachment],
    )
    log.info("Report sent — Resend ID: %s", result.id)
    return result


# ── Private helpers ──────────────────────────────────────────────────────────


def _build_body(
    *,
    briefing: str | None,
    dashboard_url: str | None,
) -> str:
    """Build the email HTML body from pieces.

    No templating engine — plain string concatenation with minimal inline HTML.
    """
    parts: list[str] = []
    parts.append("<h2>Daily Signal Scanner Report</h2>")

    if briefing:
        parts.append("<h3>Market Briefing</h3>")
        # Preserve line breaks from the briefing text
        escaped = (
            briefing.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        parts.append(f"<p style='white-space:pre-line'>{escaped}</p>")
    else:
        parts.append(
            "<p><em>No AI briefing available for this run.</em></p>"
        )

    parts.append("<p>The ranked summary workbook is attached.</p>")

    if dashboard_url:
        parts.append(
            f"<p><a href='{dashboard_url}'>View the live dashboard</a></p>"
        )

    parts.append(
        "<hr><p style='font-size:0.8em;color:#888'>"
        "Generated by Signal Scanner — automated report, do not reply.</p>"
    )

    return "\n".join(parts)


def _encode_attachment(excel_path: Path) -> dict:
    """Read and base64-encode the Excel file for the Resend API."""
    raw = excel_path.read_bytes()
    return {
        "filename": excel_path.name,
        "content": base64.b64encode(raw).decode("ascii"),
    }
