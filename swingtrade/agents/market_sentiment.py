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
        max_tokens=2048,
    )
    md = str(raw.get("discord_markdown", "")).strip() or "_No output_"
    structured = raw.get("structured")
    if not isinstance(structured, dict):
        structured = {}
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
