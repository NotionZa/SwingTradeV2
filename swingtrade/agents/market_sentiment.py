from __future__ import annotations

import logging

from anthropic import Anthropic

from swingtrade.integrations.anthropic_client import complete_json_agent
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
    bundle = bundle_macro_series()
    user = f"Session={ctx.session}\nMacro bundle (yfinance):\n{bundle}"
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
        structured={"macro_bundle": bundle, **structured},
        model_used=settings.anthropic_model_haiku,
    )
