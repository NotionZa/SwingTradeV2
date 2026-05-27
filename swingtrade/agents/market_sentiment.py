from __future__ import annotations

import logging
from datetime import date
from typing import Any

import httpx
from anthropic import Anthropic

from swingtrade.integrations.anthropic_client import complete_json_agent
from swingtrade.integrations.calendar_utils import get_next_opex
from swingtrade.integrations.finnhub_client import finnhub_market_news
from swingtrade.integrations.yfinance_data import bundle_macro_series
from swingtrade.models.agents import AgentResult, RunContext
from swingtrade.prompt_loader import load_system_prompt
from swingtrade.settings import Settings

logger = logging.getLogger(__name__)

_REASONING_FIELDS: tuple[tuple[str, str], ...] = (
    ("why_this_regime", "Why this regime"),
    ("key_tell", "Key tell"),
    ("confidence_rationale", "Confidence"),
    ("primary_catalyst", "Primary catalyst"),
    ("what_changes_the_call", "What would change the call"),
)


def _format_reasoning_section(reasoning: Any) -> str:
    """Readable markdown block from structured.reasoning for Discord."""
    if not isinstance(reasoning, dict):
        return ""
    bullets: list[str] = []
    for key, label in _REASONING_FIELDS:
        val = reasoning.get(key)
        if isinstance(val, str) and val.strip():
            bullets.append(f"**{label}:** {val.strip()}")
    if not bullets:
        return ""
    return "\n".join(
        ["", "---", "", "## Why we called it this way", ""] + bullets
    )


def _append_reasoning_to_discord(md: str, structured: dict[str, Any]) -> str:
    block = _format_reasoning_section(structured.get("reasoning"))
    if not block:
        return md
    base = md.rstrip()
    if "## Why we called it this way" in base:
        return md
    return base + block


def _format_ms_discord_from_structured(structured: dict[str, Any]) -> str:
    """Build daily-briefing markdown when the model omits or truncates discord_markdown."""
    regime = structured.get("regime")
    if not isinstance(regime, str) or not regime.strip():
        return ""
    lines = [
        "## Morning briefing",
        "",
        f"**Regime:** {regime.strip().replace('_', ' ')}",
    ]
    expl = structured.get("regime_explanation")
    if isinstance(expl, str) and expl.strip():
        lines.extend(["", expl.strip()])
    conf = structured.get("confidence_0_10")
    if conf is not None:
        lines.extend(["", f"**Confidence (0–10):** {conf}"])
    macro = structured.get("macro_summary")
    if isinstance(macro, str) and macro.strip():
        lines.extend(["", "**Macro**", macro.strip()])
    sector = structured.get("sector_strength_notes")
    if isinstance(sector, str) and sector.strip():
        lines.extend(["", "**Sectors**", sector.strip()])
    bias = structured.get("trading_bias")
    if isinstance(bias, str) and bias.strip():
        lines.extend(["", "**Trading bias**", bias.strip()])
    levels = structured.get("key_levels")
    if isinstance(levels, str) and levels.strip():
        lines.extend(["", "**Key levels**", levels.strip()])
    opex = structured.get("opex_note")
    if isinstance(opex, str) and opex.strip():
        lines.extend(["", "**OPEX**", opex.strip()])
    catalysts = structured.get("macro_catalysts")
    if isinstance(catalysts, list) and catalysts:
        lines.extend(["", "**What to watch**"])
        for item in catalysts[:6]:
            if not isinstance(item, dict):
                continue
            event = item.get("event") or item.get("name") or ""
            impact = item.get("impact") or ""
            explanation = item.get("explanation") or ""
            if not str(event).strip():
                continue
            bullet = f"- **{event}**"
            if impact:
                bullet += f" ({impact})"
            if explanation:
                bullet += f" — {explanation}"
            lines.append(bullet)
    reasoning_block = _format_reasoning_section(structured.get("reasoning"))
    if reasoning_block:
        lines.append(reasoning_block)
    lines.extend(["", "Trade well. Manage your risk."])
    return "\n".join(lines)


def _resolve_market_sentiment_discord_markdown(raw: dict[str, Any]) -> str:
    md = str(raw.get("discord_markdown", "")).strip()
    if md:
        return md
    structured = raw.get("structured")
    if isinstance(structured, dict):
        built = _format_ms_discord_from_structured(structured)
        if built:
            logger.warning(
                "Market Sentiment returned empty discord_markdown; built briefing from structured"
            )
            return built
    logger.warning(
        "Market Sentiment returned empty discord_markdown and no usable structured.regime"
    )
    return ""


def run_market_sentiment(
    settings: Settings,
    ctx: RunContext,
    client: Anthropic,
) -> AgentResult:
    yfinance_bundle = bundle_macro_series()
    market_news_headlines: list[dict[str, Any]] = []
    if not settings.finnhub_key.strip():
        logger.warning(
            "FINNHUB_KEY is unset — no market news headlines for Market Sentiment."
        )
    else:
        with httpx.Client(timeout=settings.http_timeout_seconds) as http:
            market_news_headlines = finnhub_market_news(settings, http)

    today = date.today()
    next_opex, days_to_opex = get_next_opex(today)
    next_opex_date = next_opex.isoformat()

    payload = {
        "macro_bundle": yfinance_bundle,
        "market_news_headlines": market_news_headlines,
        "next_opex_date": next_opex_date,
        "days_to_opex": days_to_opex,
    }
    user = f"Session={ctx.session}\nMarket Sentiment inputs:\n{payload}"
    raw = complete_json_agent(
        client,
        model=settings.anthropic_model_haiku,
        system=load_system_prompt("market_sentiment"),
        user=user,
        max_tokens=4096,
        timeout_seconds=120.0,
        call_label="market_sentiment",
    )
    structured = raw.get("structured")
    if not isinstance(structured, dict):
        structured = {}
    md = _resolve_market_sentiment_discord_markdown(raw) or "_No output_"
    md = _append_reasoning_to_discord(md, structured)
    return AgentResult(
        agent_id="market_sentiment",
        discord_markdown=md,
        structured={
            **structured,
            "macro_bundle": yfinance_bundle,
            "market_news_headlines": market_news_headlines,
            "next_opex_date": next_opex_date,
            "days_to_opex": days_to_opex,
        },
        model_used=settings.anthropic_model_haiku,
    )
