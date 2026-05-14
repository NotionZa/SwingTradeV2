from __future__ import annotations

import logging
from typing import Any

from anthropic import Anthropic

from swingtrade.integrations.anthropic_client import complete_json_agent
from swingtrade.integrations.ta_features import compute_ta_features
from swingtrade.integrations.yfinance_data import fetch_ohlcv, ohlcv_for_ticker
from swingtrade.models.agents import AgentResult, RunContext
from swingtrade.prompt_loader import load_system_prompt
from swingtrade.settings import Settings

logger = logging.getLogger(__name__)


def run_technical(
    settings: Settings,
    ctx: RunContext,
    client: Anthropic,
    tickers: list[str],
) -> AgentResult:
    per: dict[str, Any] = {}
    qqq = fetch_ohlcv("QQQ", period="6mo")
    qqq_last = float(qqq["Close"].iloc[-1]) if not qqq.empty else None
    for sym in tickers:
        df = ohlcv_for_ticker(sym)
        feats = compute_ta_features(df)
        rel = None
        if qqq_last and feats.get("last_close"):
            rel = float(feats["last_close"]) / qqq_last
        per[sym] = {"features": feats, "vs_qqq_close_ratio": rel}

    user = f"Session={ctx.session}\nPer-ticker features:\n{per}"
    raw = complete_json_agent(
        client,
        model=settings.anthropic_model_sonnet,
        system=load_system_prompt("technical"),
        user=user,
        max_tokens=4096,
    )
    md = str(raw.get("discord_markdown", "")).strip() or "_No TA output_"
    structured = raw.get("structured")
    if not isinstance(structured, dict):
        structured = {"scores": {}, "notes": ""}
    structured = {**structured, "inputs": per}
    return AgentResult(
        agent_id="technical_analysis",
        discord_markdown=md,
        structured=structured,
        model_used=settings.anthropic_model_sonnet,
    )
