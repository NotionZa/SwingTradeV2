from __future__ import annotations

import json
import logging
from typing import Any

from anthropic import Anthropic

from swingtrade.cio_packet import (
    CIO_MESSAGE_ACCEPTABLE_CHARS,
    CIO_MESSAGE_FAIL_CHARS,
    CIO_MESSAGE_IDEAL_CHARS,
)
from swingtrade.integrations.anthropic_client import complete_json_agent
from swingtrade.models.agents import AgentResult, RunContext, PipelineState, SessionName
from swingtrade.prompt_loader import load_system_prompt
from swingtrade.settings import Settings

logger = logging.getLogger(__name__)


def _decision_ticker(item: dict[str, Any]) -> str | None:
    raw = item.get("ticker") or item.get("symbol")
    if isinstance(raw, str) and raw.strip():
        return raw.strip().upper()
    return None


def _allowed_symbol_set(symbols: list[str]) -> set[str]:
    return {s.strip().upper() for s in symbols if s and str(s).strip()}


def _filter_cio_decisions_to_pool(
    structured: dict[str, Any],
    allowed_symbols: list[str],
) -> dict[str, Any]:
    """Keep only decisions whose ticker is in the CIO review pool."""
    allowed = _allowed_symbol_set(allowed_symbols)
    decisions = structured.get("decisions")
    if not isinstance(decisions, list):
        return structured

    accepted: list[dict[str, Any]] = []
    dropped_blank = 0
    dropped_extra: list[str] = []
    seen: set[str] = set()

    for item in decisions:
        if not isinstance(item, dict):
            continue
        sym = _decision_ticker(item)
        if not sym:
            dropped_blank += 1
            continue
        if sym not in allowed:
            dropped_extra.append(sym)
            continue
        if sym in seen:
            continue
        seen.add(sym)
        row = dict(item)
        row["ticker"] = sym
        accepted.append(row)

    if dropped_blank:
        logger.warning(
            "CIO: dropped %s decision row(s) with missing/blank ticker",
            dropped_blank,
        )
    if dropped_extra:
        logger.warning(
            "CIO: dropped %s decision(s) not in CIO_review_tickers: %s",
            len(dropped_extra),
            ", ".join(sorted(set(dropped_extra))),
        )

    return {**structured, "decisions": accepted}


def _count_cio_decisions(structured: dict[str, Any]) -> int:
    raw = structured.get("decisions")
    if not isinstance(raw, list):
        return 0
    n = 0
    for item in raw:
        if not isinstance(item, dict):
            continue
        ticker = item.get("ticker") or item.get("symbol")
        if isinstance(ticker, str) and ticker.strip():
            n += 1
    return n


def _normalize_cio_structured(raw: dict[str, Any]) -> dict[str, Any]:
    structured = raw.get("structured")
    if not isinstance(structured, dict):
        structured = {}

    decisions = structured.get("decisions")
    if not isinstance(decisions, list) or not decisions:
        top_level = raw.get("decisions")
        if isinstance(top_level, list) and top_level:
            logger.warning(
                "CIO: hoisting %s top-level decisions into structured.decisions",
                len(top_level),
            )
            structured = {**structured, "decisions": top_level}
            decisions = top_level

    # Normalize decision rows so downstream logger can key by `ticker`.
    if isinstance(decisions, list) and decisions:
        normalized: list[dict[str, Any]] = []
        for item in decisions:
            if not isinstance(item, dict):
                continue
            out = dict(item)
            if "ticker" not in out or not str(out.get("ticker") or "").strip():
                sym = out.get("symbol")
                if isinstance(sym, str) and sym.strip():
                    out["ticker"] = sym.strip().upper()
            normalized.append(out)
        structured = {**structured, "decisions": normalized}

    return structured


def _resolve_cio_discord_markdown(raw: dict[str, Any], structured: dict[str, Any]) -> str:
    md = str(raw.get("discord_markdown", "")).strip()
    if md:
        return md
    nested = structured.get("discord_markdown")
    if isinstance(nested, str) and nested.strip():
        logger.warning(
            "CIO: using structured.discord_markdown (top-level discord_markdown empty)"
        )
        return nested.strip()
    return ""


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
    cio_symbols = list(state.tickers)
    diagnostics = state.cio_packet_diagnostics(ctx.session, cio_symbols=cio_symbols)
    user = state.cio_user_message(ctx.session, cio_symbols=cio_symbols)
    msg_chars = diagnostics.get("cio_user_message_chars", len(user))
    logger.info(
        "CIO user message: %s chars (band=%s), review_tickers=%s, packet_candidates=%s",
        msg_chars,
        diagnostics.get("size_band"),
        diagnostics.get("cio_candidate_count"),
        diagnostics.get("compact_packet_candidate_count"),
    )
    logger.info("CIO packet section chars: %s", diagnostics.get("section_chars"))
    if msg_chars > CIO_MESSAGE_FAIL_CHARS:
        logger.warning(
            "CIO user message %s chars exceeds fail gate %s — expect degraded CIO output",
            msg_chars,
            CIO_MESSAGE_FAIL_CHARS,
        )
    elif msg_chars > CIO_MESSAGE_ACCEPTABLE_CHARS:
        logger.warning(
            "CIO user message %s chars above acceptable %s (ideal %s)",
            msg_chars,
            CIO_MESSAGE_ACCEPTABLE_CHARS,
            CIO_MESSAGE_IDEAL_CHARS,
        )
    raw = complete_json_agent(
        client,
        model=settings.anthropic_model_opus,
        system=load_system_prompt("cio"),
        user=user,
        max_tokens=8192,
        call_label="cio",
    )
    structured = _normalize_cio_structured(raw)
    raw_decision_count = _count_cio_decisions(structured)
    structured = _filter_cio_decisions_to_pool(structured, cio_symbols)
    md = _resolve_cio_discord_markdown(raw, structured) or "_No CIO output_"

    decision_count = _count_cio_decisions(structured)
    logger.info(
        "CIO decisions returned: %s raw -> %s accepted (pool=%s)",
        raw_decision_count,
        decision_count,
        len(cio_symbols),
    )
    if decision_count == 0:
        if structured.get("parse_error"):
            logger.warning(
                "CIO returned 0 decisions (JSON parse error: %s)",
                structured.get("parse_error"),
            )
        else:
            logger.warning(
                "CIO returned 0 decisions (expected up to %s for CIO pool)",
                len(cio_symbols),
            )
    elif cio_symbols and decision_count < len(cio_symbols):
        logger.warning(
            "CIO accepted %s decisions but CIO pool has %s symbols (missing decisions for some pool tickers)",
            decision_count,
            len(cio_symbols),
        )

    raw_prefix = str(raw.get("structured", {}).get("raw_prefix", ""))[:300]
    if decision_count == 0 and not structured.get("parse_error"):
        # Log the first 300 chars of the raw structured blob to aid diagnosis.
        logger.warning(
            "CIO 0 decisions: raw structured keys=%s raw_prefix=%r",
            list(raw.get("structured", {}).keys())[:10] if isinstance(raw.get("structured"), dict) else "not-dict",
            raw_prefix or str(raw)[:300],
        )
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
