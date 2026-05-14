from __future__ import annotations

import json
import logging
from typing import Any, Literal

from anthropic import Anthropic

from swingtrade.agents.cio import run_cio
from swingtrade.agents.hard_veto import run_hard_veto
from swingtrade.agents.market_sentiment import run_market_sentiment
from swingtrade.agents.sentiment import run_sentiment
from swingtrade.agents.technical import run_technical
from swingtrade.integrations.http import post_discord_webhook, webhook_client
from swingtrade.models.agents import AgentResult, PipelineState, RunContext, SessionName
from swingtrade.settings import Settings, get_settings
from swingtrade.universe_loader import load_universe_yaml, merge_watchlist_into_universe
from swingtrade.watchlist_store import load_watchlist_yaml

logger = logging.getLogger(__name__)


def context_only_tickers(watchlist: dict[str, list[str]]) -> set[str]:
    ctx = set(watchlist.get("Context proxies", []))
    other: set[str] = set()
    for name, xs in watchlist.items():
        if name == "Context proxies":
            continue
        for t in xs:
            other.add(t)
    return {t for t in ctx if t not in other}


def _stub(agent_id: str, note: str) -> AgentResult:
    return AgentResult(
        agent_id=agent_id,
        discord_markdown=f"**[{agent_id}]** _stub_ — {note}",
        structured={"stub": True},
        model_used="stub",
    )


def _anthropic_client(settings: Settings) -> Anthropic | None:
    if not settings.anthropic_api_key.strip():
        return None
    from swingtrade.integrations.anthropic_client import make_anthropic_client

    return make_anthropic_client(settings)


def format_news_digest(sentiment_structured: dict[str, Any]) -> str:
    raw = sentiment_structured.get("raw_bundle") or {}
    per = (raw.get("per_ticker") or {}) if isinstance(raw, dict) else {}
    lines = ["**Market news digest** (headlines only)"]
    if not isinstance(per, dict):
        return "\n".join(lines)
    for sym, block in per.items():
        if not isinstance(block, dict):
            continue
        news = block.get("news") or []
        lines.append(f"`{sym}`")
        if not news:
            lines.append("- _(no headlines)_")
            continue
        for item in news[:4]:
            if not isinstance(item, dict):
                continue
            title = item.get("title") or ""
            src = item.get("source") or ""
            lines.append(f"- {title} _({src})_")
    return "\n".join(lines)


def _survivors_after_veto(hv: AgentResult, trade: list[str]) -> list[str]:
    s = hv.structured.get("survivors")
    if isinstance(s, list) and all(isinstance(x, str) for x in s):
        return list(s)
    killed: set[str] = set()
    for r in hv.structured.get("vetoes") or []:
        if isinstance(r, dict) and r.get("killed") and isinstance(r.get("symbol"), str):
            killed.add(r["symbol"])
    return [t for t in trade if t not in killed]


SingleAgentName = Literal[
    "market_sentiment",
    "hard_veto",
    "technical_analysis",
    "sentiment",
    "cio",
]


def run_single_agent(
    *,
    agent: SingleAgentName,
    session: SessionName,
    dry_run: bool = False,
    max_tickers: int = 30,
    settings: Settings | None = None,
) -> None:
    """Run one pipeline agent and POST only that agent's usual Discord payload(s).

    Upstream agents are executed **without** Discord when their outputs are required
    (e.g. survivors after hard veto for technical / sentiment / CIO).
    """
    settings = settings or get_settings()
    ctx = RunContext(session=session, dry_run=dry_run)

    wl = load_watchlist_yaml(settings.watchlist_path())
    uni = load_universe_yaml(settings.universe_path())
    merged = merge_watchlist_into_universe(uni, wl)
    ctx_only = context_only_tickers(wl)
    trade = [t for t in merged if t not in ctx_only][:max_tickers]

    client = _anthropic_client(settings)
    http = webhook_client(settings.http_timeout_seconds)

    try:
        if agent == "market_sentiment":
            if client:
                ms = run_market_sentiment(settings, ctx, client)
            else:
                ms = _stub("market_sentiment", "ANTHROPIC_API_KEY missing")
            if session == "pre_market":
                post_discord_webhook(
                    http,
                    settings.discord_webhook_daily_briefing,
                    ms.discord_markdown,
                    dry_run=dry_run,
                )
            else:
                post_discord_webhook(
                    http,
                    settings.discord_webhook_earnings_flow,
                    "**Market Sentiment (standalone)**\n" + ms.discord_markdown,
                    dry_run=dry_run,
                )
            return

        if agent == "hard_veto":
            hv = run_hard_veto(settings, trade, wl)
            post_discord_webhook(
                http,
                settings.discord_webhook_earnings_flow,
                "**Earnings / veto (standalone)**\n" + hv.discord_markdown,
                dry_run=dry_run,
            )
            return

        hv = run_hard_veto(settings, trade, wl)
        survivors = _survivors_after_veto(hv, trade)

        if agent == "technical_analysis":
            if client:
                ta = run_technical(settings, ctx, client, survivors)
            else:
                ta = _stub("technical_analysis", "ANTHROPIC_API_KEY missing")
            post_discord_webhook(
                http,
                settings.discord_webhook_watchlist,
                ta.discord_markdown,
                dry_run=dry_run,
            )
            return

        if agent == "sentiment":
            if client:
                se = run_sentiment(settings, ctx, client, survivors)
            else:
                se = _stub("sentiment", "ANTHROPIC_API_KEY missing")
            post_discord_webhook(
                http,
                settings.discord_webhook_macro_tech,
                se.discord_markdown,
                dry_run=dry_run,
            )
            post_discord_webhook(
                http,
                settings.discord_webhook_market_news,
                format_news_digest(se.structured),
                dry_run=dry_run,
            )
            return

        # cio — run prior agents for context only (no Discord until CIO)
        state = PipelineState(tickers=trade, watchlist_by_category=wl)
        if client:
            ms = run_market_sentiment(settings, ctx, client)
        else:
            ms = _stub("market_sentiment", "ANTHROPIC_API_KEY missing")
        state.add(ms.agent_id, ms)
        state.add(hv.agent_id, hv)
        state.tickers = survivors
        if client:
            ta = run_technical(settings, ctx, client, survivors)
        else:
            ta = _stub("technical_analysis", "ANTHROPIC_API_KEY missing")
        state.add(ta.agent_id, ta)
        if client:
            se = run_sentiment(settings, ctx, client, survivors)
        else:
            se = _stub("sentiment", "ANTHROPIC_API_KEY missing")
        state.add(se.agent_id, se)
        if client:
            cio = run_cio(settings, ctx, state, client)
        else:
            cio = _stub("cio", "ANTHROPIC_API_KEY missing")
        post_discord_webhook(
            http,
            settings.discord_webhook_trade_setups,
            cio.discord_markdown,
            dry_run=dry_run,
        )
        if session == "pre_market":
            risk_body = (
                "## Risk management\n"
                + cio.discord_markdown
                + "\n\n```json\n"
                + json.dumps(cio.structured, indent=2)
                + "\n```"
            )
            post_discord_webhook(
                http,
                settings.discord_webhook_risk_management,
                risk_body,
                dry_run=dry_run,
            )
    finally:
        http.close()


def run_pipeline(
    *,
    session: SessionName,
    dry_run: bool = False,
    max_tickers: int = 30,
    settings: Settings | None = None,
) -> None:
    settings = settings or get_settings()
    ctx = RunContext(session=session, dry_run=dry_run)

    wl = load_watchlist_yaml(settings.watchlist_path())
    uni = load_universe_yaml(settings.universe_path())
    merged = merge_watchlist_into_universe(uni, wl)
    ctx_only = context_only_tickers(wl)
    trade = [t for t in merged if t not in ctx_only][:max_tickers]

    client = _anthropic_client(settings)

    state = PipelineState(tickers=trade, watchlist_by_category=wl)

    http = webhook_client(settings.http_timeout_seconds)
    try:
        # 1) Market Sentiment
        if client:
            ms = run_market_sentiment(settings, ctx, client)
        else:
            ms = _stub("market_sentiment", "ANTHROPIC_API_KEY missing")
        state.add(ms.agent_id, ms)
        if session == "pre_market":
            post_discord_webhook(
                http,
                settings.discord_webhook_daily_briefing,
                ms.discord_markdown,
                dry_run=dry_run,
            )

        # 2) Hard Veto
        hv = run_hard_veto(settings, trade, wl)
        state.add(hv.agent_id, hv)
        survivors = _survivors_after_veto(hv, trade)
        state.tickers = survivors
        post_discord_webhook(
            http,
            settings.discord_webhook_earnings_flow,
            "**Market Sentiment (snapshot)**\n"
            + ms.discord_markdown
            + "\n\n**Earnings / veto**\n"
            + hv.discord_markdown,
            dry_run=dry_run,
        )

        # 3) Technical
        if client:
            ta = run_technical(settings, ctx, client, survivors)
        else:
            ta = _stub("technical_analysis", "ANTHROPIC_API_KEY missing")
        state.add(ta.agent_id, ta)
        post_discord_webhook(
            http,
            settings.discord_webhook_watchlist,
            ta.discord_markdown,
            dry_run=dry_run,
        )

        # 4) Sentiment
        if client:
            se = run_sentiment(settings, ctx, client, survivors)
        else:
            se = _stub("sentiment", "ANTHROPIC_API_KEY missing")
        state.add(se.agent_id, se)
        post_discord_webhook(
            http,
            settings.discord_webhook_macro_tech,
            se.discord_markdown,
            dry_run=dry_run,
        )
        post_discord_webhook(
            http,
            settings.discord_webhook_market_news,
            format_news_digest(se.structured),
            dry_run=dry_run,
        )

        # 5) CIO
        if client:
            cio = run_cio(settings, ctx, state, client)
        else:
            cio = _stub("cio", "ANTHROPIC_API_KEY missing")
        state.add(cio.agent_id, cio)
        post_discord_webhook(
            http,
            settings.discord_webhook_trade_setups,
            cio.discord_markdown,
            dry_run=dry_run,
        )
        if session == "pre_market":
            risk_body = (
                "## Risk management\n"
                + cio.discord_markdown
                + "\n\n```json\n"
                + json.dumps(cio.structured, indent=2)
                + "\n```"
            )
            post_discord_webhook(
                http,
                settings.discord_webhook_risk_management,
                risk_body,
                dry_run=dry_run,
            )
    finally:
        http.close()
