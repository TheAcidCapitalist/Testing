"""Live Resend delivery probe — run locally, NOT in CI.

Validates that send_report() actually delivers through the live Resend API:
auth, from-address rules, attachment encoding, and inbox delivery. Mocked
tests cannot prove any of this — only a real send can.

Usage
-----
    export RESEND_API_KEY=...                # your Resend key
    export PROBE_TO=you@your-account-email   # MUST be your Resend account
                                             # email when using onboarding@resend.dev
    uv run python scripts/resend_probe.py

Notes
-----
- Without a verified domain, Resend only sends from ``onboarding@resend.dev``
  and only delivers to your own Resend account email. Override the sender with
  PROBE_FROM once you have a verified domain.
- On success it prints the Resend message id. Then check the inbox for the
  email and the attached .xlsx. Record the outcome in spec/resend-probe-notes.md.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from scanner.report.email import SendError, send_report  # noqa: E402


def _throwaway_xlsx() -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Ranked Summary"
    ws.append(["Ticker", "Direction", "Rank Score"])
    ws.append(["PROBE", "buy", 0.99])
    p = Path(tempfile.gettempdir()) / "resend_probe.xlsx"
    wb.save(p)
    return p


def main() -> int:
    if not os.environ.get("RESEND_API_KEY"):
        print("RESEND_API_KEY not set — aborting.")
        return 2

    to = os.environ.get("PROBE_TO")
    if not to:
        print("PROBE_TO not set (your Resend account email) — aborting.")
        return 2

    from_addr = os.environ.get("PROBE_FROM", "onboarding@resend.dev")
    xlsx = _throwaway_xlsx()

    try:
        result = send_report(
            briefing="Resend live probe — if you see this with an .xlsx attached, delivery works.",
            excel_path=xlsx,
            recipients=[to],
            subject="[Signal Scanner] Resend live probe",
            from_addr=from_addr,
            dashboard_url="https://example.com/dashboard",
        )
    except (SendError, ValueError) as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}")
        return 1

    print(f"SUCCESS — Resend id: {result.id}")
    print(f"Now check {to} for the email and the attached .xlsx.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
