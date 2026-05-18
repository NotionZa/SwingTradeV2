from __future__ import annotations

import logging
from typing import Any

import httpx
from anthropic import Anthropic

from swingtrade.integrations.anthropic_client import complete_json_agent
from swingtrade.integrations.newsapi_client import newsapi_macro_merged
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
    macro_queries = settings.macro_news_queries()
    macro_headlines: list[dict[str, Any]] = []
    if not settings.newsapi_key.strip():
        logger.warning(
            "NEWSAPI_KEY is unset — no macro headlines for Market Sentiment "
            "(set NEWSAPI_KEY or NEWS_API_KEY in .env)."
        )
    else:
        with httpx.Client(timeout=settings.http_timeout_seconds) as http:
            macro_articles = newsapi_macro_merged(
                settings,
                http,
                macro_queries,
                per_query_size=6,
                max_total=20,
            )
            macro_headlines = [
                {"title": a.get("title"), "source": (a.get("source") or {}).get("name")}
                for a in macro_articles
            ]

    payload = {
        "macro_bundle": yfinance_bundle,
        "macro_queries": macro_queries,
        "macro_headlines": macro_headlines,
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
            "macro_bundle": yfinance_bundle,
            "macro_queries": macro_queries,
            "macro_headlines": macro_headlines,
            **structured,
        },
        model_used=settings.anthropic_model_haiku,
    )
