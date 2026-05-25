from __future__ import annotations

import json
import logging
from typing import Any

from anthropic import Anthropic

from swingtrade.integrations.anthropic_client import complete_json_agent
from swingtrade.models.agents import AgentResult, RunContext, PipelineState, SessionName
from swingtrade.prompt_loader import load_system_prompt
from swingtrade.settings import Settings

logger = logging.getLogger(__name__)


def _summary_int(summary: dict[str, Any], key: str) -> int:
    v = summary.get(key)
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    return 0


def _summary_str(summary: dict[str, Any], key: str, default: str = "Unknown") -> str:
    v = summary.get(key)
    if v is None:
        return default
    s = str(v).strip()
    return s if s else default


def build_cio_risk_markdown(result: AgentResult, session: str | SessionName) -> str:
    """Compact risk-management Discord post from CIO structured output (never raw JSON)."""
    structured = result.structured if isinstance(result.structured, dict) else {}
    summary = structured.get("summary")
    if not isinstance(summary, dict):
        summary = {}

    notes = structured.get("notes")
    notes_text = notes.strip() if isinstance(notes, str) else ""
    session_message = summary.get("session_message")
    session_message_text = (
        session_message.strip() if isinstance(session_message, str) else ""
    )
    risk_notes = notes_text or session_message_text or "_No risk notes._"

    highest = summary.get("highest_conviction_ticker")
    if highest is None or (isinstance(highest, str) and not highest.strip()):
        highest_display = "None"
    else:
        highest_display = str(highest).strip()

    session_label = str(session).replace("_", " ").title()

    return (
        f"🛡️ **SwingTrader — Risk Summary | {session_label}**\n\n"
        f"**Risk Level:** {_summary_str(summary, 'overall_risk_level')}\n"
        f"**Regime:** {_summary_str(summary, 'market_regime')}\n"
        f"**Tech Bias:** {_summary_str(summary, 'tech_bias')}\n\n"
        f"**Decision Counts**\n"
        f"BUY: {_summary_int(summary, 'buy_count')}\n"
        f"WATCH: {_summary_int(summary, 'watch_count')}\n"
        f"PASS: {_summary_int(summary, 'pass_count')}\n"
        f"BLOCKED: {_summary_int(summary, 'blocked_count')}\n\n"
        f"**Highest Conviction:** {highest_display}\n\n"
        f"**Risk Notes**\n"
        f"{risk_notes}\n\n"
        f"**Instruction**\n"
        "No new positions unless confirmation conditions are met. "
        "Reduce sizing if regime/volume risk remains elevated."
    )


def run_cio(
    settings: Settings,
    ctx: RunContext,
    state: PipelineState,
    client: Anthropic,
) -> AgentResult:
    user = state.cio_user_message(ctx.session, cio_symbols=state.tickers)
    logger.debug(
        "CIO user payload: %s agent structured blobs, %s chars",
        len(state.prior_structured),
        len(user),
    )
    raw = complete_json_agent(
        client,
        model=settings.anthropic_model_opus,
        system=load_system_prompt("cio"),
        user=user,
        max_tokens=8192,
    )
    md = str(raw.get("discord_markdown", "")).strip() or "_No CIO output_"
    structured = raw.get("structured")
    if not isinstance(structured, dict):
        structured = {}
    logger.debug(
        "CIO structured output (internal): %s",
        json.dumps(structured, indent=2, ensure_ascii=False, default=str),
    )
    return AgentResult(
        agent_id="cio",
        discord_markdown=md,
        structured=structured,
        model_used=settings.anthropic_model_opus,
    )
