from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import httpx
from anthropic import Anthropic

from swingtrade.integrations.anthropic_client import complete_json_agent
from swingtrade.integrations.finnhub_client import finnhub_company_news
from swingtrade.integrations.reddit_client import make_reddit_client, reddit_search_snippets
from swingtrade.models.agents import AgentResult, RunContext
from swingtrade.prompt_loader import load_system_prompt
from swingtrade.settings import Settings

logger = logging.getLogger(__name__)


def run_sentiment(
    settings: Settings,
    ctx: RunContext,
    client: Anthropic,
    tickers: list[str],
) -> AgentResult:
    if not settings.finnhub_key.strip():
        logger.warning(
            "FINNHUB_KEY is unset — no company news headlines for Sentiment."
        )
    reddit = make_reddit_client(settings)
    to_date = date.today()
    from_date = to_date - timedelta(days=7)
    bundle: dict[str, Any] = {}
    with httpx.Client(timeout=settings.http_timeout_seconds) as http:
        per: dict[str, Any] = {}
        for sym in tickers:
            articles = finnhub_company_news(
                settings,
                http,
                sym,
                from_date,
                to_date,
                limit=5,
            )
            per[sym] = {
                "symbol": sym,
                "news_from": from_date.isoformat(),
                "news_to": to_date.isoformat(),
                "news": articles,
                "reddit": reddit_search_snippets(reddit, sym, limit=4),
            }
    bundle["per_ticker"] = per

    with_news = sum(1 for v in per.values() if v.get("news"))
    logger.info(
        "Sentiment news: tickers=%s with_any_headline=%s (Finnhub company-news, last 7 days)",
        len(tickers),
        with_news,
    )

    user = f"Session={ctx.session}\nSentiment bundle:\n{bundle}"
    raw = complete_json_agent(
        client,
        model=settings.anthropic_model_sonnet,
        system=load_system_prompt("sentiment"),
        user=user,
        max_tokens=4096,
    )
    md = str(raw.get("discord_markdown", "")).strip() or "_No sentiment output_"
    structured = raw.get("structured")
    if not isinstance(structured, dict):
        structured = {}
    structured = {**structured, "raw_bundle": bundle}
    return AgentResult(
        agent_id="sentiment",
        discord_markdown=md,
        structured=structured,
        model_used=settings.anthropic_model_sonnet,
    )
