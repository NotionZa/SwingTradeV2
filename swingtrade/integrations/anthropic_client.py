from __future__ import annotations

import json
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
    timeout_seconds: float | None = None,
    call_label: str | None = None,
) -> dict[str, Any]:
    """Ask model for a single JSON object in the assistant text."""
    api = client
    if timeout_seconds is not None:
        api = client.with_options(timeout=timeout_seconds)
    msg = api.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    if call_label:
        from swingtrade.anthropic_usage import record_call

        record_call(label=call_label, model=model, message=msg)
    text = _extract_text(msg)
    from swingtrade.json_utils import (
        extract_json_object,
        extract_legacy_discord_markdown,
        sanitize_discord_markdown,
    )

    try:
        raw = extract_json_object(text)
    except Exception as e:
        logger.warning("JSON parse failed, wrapping raw text: %s", e)
        legacy_md = extract_legacy_discord_markdown(text)
        salvage = legacy_md or sanitize_discord_markdown(text[:18000])
        if salvage.startswith("{") and '"discord_markdown"' in salvage:
            try:
                obj = json.loads(salvage)
                if isinstance(obj, dict) and isinstance(obj.get("discord_markdown"), str):
                    salvage = sanitize_discord_markdown(obj["discord_markdown"])
            except json.JSONDecodeError:
                pass
        return {
            "discord_markdown": salvage or "_Parse error — see logs._",
            "structured": {"parse_error": str(e), "raw_prefix": text[:500]},
        }
    dm = raw.get("discord_markdown")
    dm = sanitize_discord_markdown(dm)
    if not dm:
        legacy_md = extract_legacy_discord_markdown(text)
        if legacy_md:
            dm = legacy_md
    raw["discord_markdown"] = dm
    return raw


def _extract_text(msg: Any) -> str:
    parts: list[str] = []
    for block in getattr(msg, "content", []) or []:
        btype = getattr(block, "type", None)
        if btype == "text":
            parts.append(getattr(block, "text", ""))
    return "\n".join(parts).strip()
