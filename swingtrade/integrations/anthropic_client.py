from __future__ import annotations

import logging
from typing import Any

from anthropic import Anthropic

from swingtrade.settings import Settings

logger = logging.getLogger(__name__)


def make_anthropic_client(settings: Settings) -> Anthropic:
    if not settings.anthropic_api_key.strip():
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    return Anthropic(
        api_key=settings.anthropic_api_key,
        timeout=settings.anthropic_timeout_seconds,
        max_retries=2,
    )


def complete_json_agent(
    client: Anthropic,
    *,
    model: str,
    system: str,
    user: str,
    max_tokens: int = 4096,
) -> dict[str, Any]:
    """Ask model for a single JSON object in the assistant text."""
    msg = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = _extract_text(msg)
    from swingtrade.json_utils import extract_json_object

    try:
        return extract_json_object(text)
    except Exception as e:
        logger.warning("JSON parse failed, wrapping raw text: %s", e)
        return {
            "discord_markdown": text[:18000],
            "structured": {"parse_error": str(e), "raw_prefix": text[:500]},
        }


def _extract_text(msg: Any) -> str:
    parts: list[str] = []
    for block in getattr(msg, "content", []) or []:
        btype = getattr(block, "type", None)
        if btype == "text":
            parts.append(getattr(block, "text", ""))
    return "\n".join(parts).strip()
