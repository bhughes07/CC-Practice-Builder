"""Optional: a short coach's note written by Claude on top of the computed numbers.

All arithmetic happens in Python. Claude only receives the aggregated summary
(category totals, detected subscriptions, ranked insights) — no account names,
no account numbers, no raw bank descriptions — and writes the narrative.

Requires `pip install anthropic` and ANTHROPIC_API_KEY (or an `ant auth login`
profile).
"""

from __future__ import annotations

import json
import sys
from typing import Any

SYSTEM_PROMPT = """You are a warm, candid household budget coach. You write the short note at the top of a \
family's budget report. The reader is usually a couple reviewing it together.

Ground rules:
- Use only the numbers in the report data. Never invent amounts, merchants, or trends. If you combine numbers, \
keep the math simple and grounded in the data.
- Never suggest reducing giving, tithing, charitable donations, or saving/investing. Treat those as commitments \
to protect. Categories marked "protected": true are off-limits for cuts.
- Be specific. Name the category or merchant, the dollar amount, and one concrete next step. Pick the 2-3 \
changes with the biggest dollar impact rather than a long list; the full list is already in the report.
- Lead with something that's genuinely going well, then be honest about the problems without shaming.
- Plain, friendly language. No jargon, no generic tips that the data doesn't support.
- This is budgeting coaching, not professional financial, tax, or investment advice.

Field guidance: headline is one sentence. summary is 2-4 sentences. Each priority's impact is a short dollar \
phrase like "$530/mo". discussion_question is one question the household could talk through together this week."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "summary": {"type": "string"},
        "priorities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "why": {"type": "string"},
                    "impact": {"type": "string"},
                },
                "required": ["action", "why", "impact"],
                "additionalProperties": False,
            },
        },
        "encouragement": {"type": "string"},
        "discussion_question": {"type": "string"},
    },
    "required": ["headline", "summary", "priorities", "encouragement", "discussion_question"],
    "additionalProperties": False,
}


def _warn(msg: str) -> None:
    print(f"[ai] {msg} — continuing without the coach's note.", file=sys.stderr)


def coach_note(summary: dict, kind: str, config: dict, client: Any = None) -> dict | None:
    """Return {headline, summary, priorities[], encouragement, discussion_question} or None."""
    ai = config.get("ai", {})
    try:
        import anthropic
    except ImportError:
        _warn("The 'anthropic' package isn't installed (pip install anthropic)")
        return None

    client = client or anthropic.Anthropic()
    data = json.dumps(summary, default=str, indent=1)
    try:
        resp = client.beta.messages.create(
            model=ai.get("model", "claude-opus-5-5"),
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",  # if the request is declined, the API retries on a recommended fallback model
            system=SYSTEM_PROMPT,
            output_config={"effort": ai.get("effort", "high"),
                           "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{
                "role": "user",
                "content": f"Here is the data for this {kind} budget report:\n\n<report_data>\n{data}\n"
                           f"</report_data>\n\nWrite the coach's note.",
            }],
        )
    except anthropic.AuthenticationError:
        _warn("Claude API authentication failed (set ANTHROPIC_API_KEY or run `ant auth login`)")
        return None
    except anthropic.RateLimitError:
        _warn("Claude API rate limit reached")
        return None
    except anthropic.APIStatusError as err:
        _warn(f"Claude API error {err.status_code}: {err.message}")
        return None
    except anthropic.APIConnectionError:
        _warn("Couldn't reach the Claude API")
        return None

    if resp.stop_reason == "refusal":
        _warn("Claude declined to write this note")
        return None
    if resp.stop_reason == "max_tokens":
        _warn("The response was cut off")
        return None
    text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), None)
    if not text:
        _warn("Empty response")
        return None
    try:
        note = json.loads(text)
    except json.JSONDecodeError:
        _warn("Couldn't parse the response")
        return None
    note["priorities"] = note.get("priorities", [])[:3]
    return note
