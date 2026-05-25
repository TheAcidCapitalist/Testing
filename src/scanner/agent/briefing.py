"""Haiku briefing generator — the first (and only) LLM in the pipeline.

Takes the canonical dashboard dict produced by ``dashboard_json.build_dashboard_dict``
and returns a short, factual daily summary grounded exclusively in the dict's values.

Hard constraints
----------------
1. **JSON-only input.** Reads nothing beyond the dict — no DuckDB, no prices,
   no storage.
2. **Fail-soft.** Never raises.  On *any* failure (missing key, network error,
   empty/malformed response) it logs a warning and returns ``None``.  The caller
   treats ``None`` as "no briefing section" and the report ships without it.
"""

from __future__ import annotations

import json
import logging
import os
from textwrap import dedent

log = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
"""Haiku model identifier.  Override via the ``model`` parameter."""

MAX_TOKENS = 1024
"""Maximum tokens for the briefing response."""


def generate_briefing(
    data: dict,
    *,
    client: object | None = None,
    model: str = DEFAULT_MODEL,
) -> str | None:
    """Generate a short daily briefing from the canonical dashboard dict.

    Parameters
    ----------
    data:
        The dict returned by ``build_dashboard_dict()``.
    client:
        An ``anthropic.Anthropic`` instance.  When ``None``, a real client
        is built from the ``ANTHROPIC_API_KEY`` environment variable.
    model:
        The Anthropic model to use (default: :data:`DEFAULT_MODEL`).

    Returns
    -------
    str | None
        The briefing text, or ``None`` if generation failed for any reason.
    """
    try:
        return _generate(data, client=client, model=model)
    except Exception:
        log.warning("Briefing generation failed", exc_info=True)
        return None


# ── Internals ────────────────────────────────────────────────────────────────


def _generate(
    data: dict,
    *,
    client: object | None,
    model: str,
) -> str | None:
    """Core generation logic — allowed to raise; caller wraps in try/except."""
    # Lazy import so the SDK is only loaded when actually generating.
    import anthropic  # noqa: F811

    # Build or validate the client
    if client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            log.warning("ANTHROPIC_API_KEY not set — skipping briefing")
            return None
        client = anthropic.Anthropic(api_key=api_key)

    # ── Prepare the prompt payload ───────────────────────────────────────
    payload = _build_payload(data)
    if payload is None:
        return None

    system_prompt = dedent("""\
        You are a concise equity-signal analyst producing a daily briefing.
        You receive a JSON snapshot of today's scan results.  Your job:

        1. State the run date, universe scope, and how many tickers were scored.
        2. Summarise the direction breakdown (buy / sell / neutral counts).
        3. For each non-neutral ticker (strongest first by rank_score):
           - Name (if available), ticker, exchange, direction, rank_score.
           - Key signals firing (from signals_firing list).
           - Vol/volume confirmation status.
           - MTF alignment fraction (if > 0).
           - Sector context (if available).
        4. Note any notable patterns (e.g. all-buy, heavy neutral, sector clustering).

        Rules:
        - Be factual.  Only cite values present in the JSON — never hallucinate data.
        - Keep it under 300 words.
        - Use plain text, not markdown.  No headers, bullets, or formatting beyond
          line breaks for readability.
        - If the scan is empty (0 tickers scored), say so in one sentence.
    """)

    response = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": payload}],
    )

    # Extract text from the response
    text = _extract_text(response)
    if not text:
        log.warning("Empty or unparseable response from model")
        return None

    return text


def _build_payload(data: dict) -> str | None:
    """Build the user-message payload: envelope + non-neutral tickers only.

    Neutral tickers are excluded to save tokens — they carry no actionable
    signal and the briefing wouldn't mention them.
    """
    try:
        meta = data.get("meta", {})
        tickers = data.get("tickers", [])
    except (AttributeError, TypeError):
        log.warning("Malformed dashboard dict — cannot build payload")
        return None

    non_neutral = [
        _slim_ticker(t) for t in tickers if t.get("direction") != "neutral"
    ]

    payload = {
        "run_date": meta.get("run_date"),
        "scope": meta.get("scope"),
        "combination_name": meta.get("combination_name"),
        "n_tickers_scored": meta.get("n_tickers_scored", 0),
        "n_tickers_universe": meta.get("n_tickers_universe", 0),
        "n_buy": meta.get("n_buy", 0),
        "n_sell": meta.get("n_sell", 0),
        "n_neutral": meta.get("n_neutral", 0),
        "actionable_tickers": non_neutral,
    }

    return json.dumps(payload, default=str)


def _slim_ticker(t: dict) -> dict:
    """Extract only the fields the briefing model needs from a ticker dict."""
    return {
        "ticker": t.get("ticker"),
        "exchange": t.get("exchange"),
        "direction": t.get("direction"),
        "rank_score": t.get("rank_score"),
        "combo_score": t.get("combo_score"),
        "agreement_count": t.get("agreement_count"),
        "n_trade_indicators": t.get("n_trade_indicators"),
        "signals_firing": t.get("signals_firing", []),
        "vol_confirmation": t.get("vol_confirmation"),
        "volume_confirmation": t.get("volume_confirmation"),
        "days_since_breakout": t.get("days_since_breakout"),
        "alignment_fraction": (t.get("mtf_alignment") or {}).get(
            "alignment_fraction", 0.0
        ),
        "name": (t.get("meta") or {}).get("name"),
        "sector": (t.get("meta") or {}).get("sector"),
    }


def _extract_text(response: object) -> str | None:
    """Pull plain text from an Anthropic Messages response."""
    try:
        for block in response.content:
            if getattr(block, "type", None) == "text":
                text = block.text.strip()
                if text:
                    return text
    except (AttributeError, TypeError):
        pass
    return None
