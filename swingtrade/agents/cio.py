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

# Opus CIO JSON for 12 tickers can exceed 8k output tokens when verbose; 12k reduces truncation.
CIO_MAX_OUTPUT_TOKENS = 12288

_CIO_DISCORD_MAX_BUY_DETAIL = 3
_CIO_DISCORD_MAX_WATCH_DETAIL = 6
_CIO_DISCORD_FIELD_MAX_CHARS = 200
_CIO_DISCORD_BUY_THESIS_MAX_CHARS = 300
_CIO_DISCORD_BUY_REASON_MAX_CHARS = 220
_CIO_DISCORD_BUY_ACTION_MAX_CHARS = 180


def _extract_cio_decisions(raw: Any) -> tuple[list[dict[str, Any]], str]:
    """Extract decision rows from multiple possible model output shapes.

    Returns (decisions, shape_label).
    """
    # 1) expected: {"structured": {"decisions": [...]}, ...}
    if isinstance(raw, dict):
        structured = raw.get("structured")
        if isinstance(structured, dict):
            dec = structured.get("decisions")
            if isinstance(dec, list):
                return [d for d in dec if isinstance(d, dict)], "structured.decisions"

        # 2) top-level: {"decisions": [...]}
        dec2 = raw.get("decisions")
        if isinstance(dec2, list):
            return [d for d in dec2 if isinstance(d, dict)], "top_level.decisions"

        # 3) single decision object: {"ticker": "...", "decision": "...", ...}
        if isinstance(raw.get("decision"), str) and (
            isinstance(raw.get("ticker"), str) or isinstance(raw.get("symbol"), str)
        ):
            return [raw], "top_level.decision_object"

    # 4) top-level list: [{"ticker": "...", ...}, ...]
    if isinstance(raw, list):
        return [d for d in raw if isinstance(d, dict)], "top_level.list"

    return [], "none"


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


def _normalize_cio_structured(raw: Any) -> tuple[dict[str, Any], str]:
    """Normalize CIO structured output and return (structured, shape_label)."""
    shape = "unknown"
    structured: dict[str, Any] = {}

    if isinstance(raw, dict):
        s = raw.get("structured")
        if isinstance(s, dict):
            structured = dict(s)

    decisions, shape = _extract_cio_decisions(raw)
    if shape != "structured.decisions" and decisions:
        logger.warning("CIO normalization used fallback shape: %s", shape)

    # Normalize decision rows so downstream logger can key by `ticker`.
    normalized: list[dict[str, Any]] = []
    for item in decisions:
        out = dict(item)
        if "ticker" not in out or not str(out.get("ticker") or "").strip():
            sym = out.get("symbol")
            if isinstance(sym, str) and sym.strip():
                out["ticker"] = sym.strip().upper()
        normalized.append(out)

    structured = {**structured, "decisions": normalized}
    return structured, shape


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


def _cap_discord_field(value: Any, *, max_chars: int = _CIO_DISCORD_FIELD_MAX_CHARS) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float, bool)):
        text = str(value)
    elif isinstance(value, str):
        text = value.strip()
    else:
        text = str(value).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _decision_cio_score(row: dict[str, Any]) -> float:
    raw = row.get("cio_score")
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw.strip())
        except ValueError:
            return float("-inf")
    return float("-inf")


def _is_system_fallback_row(row: dict[str, Any]) -> bool:
    return str(row.get("source") or "").strip().lower() == "system_fallback"


def _format_targets_row(row: dict[str, Any]) -> str:
    target = row.get("target")
    if target is not None and str(target).strip():
        return _cap_discord_field(target, max_chars=80)
    targets = row.get("targets")
    if isinstance(targets, list):
        parts = [_cap_discord_field(t, max_chars=60) for t in targets if t is not None and str(t).strip()]
        return ", ".join(parts[:3])
    return ""


def _format_invalidation(row: dict[str, Any]) -> str:
    inv = row.get("invalidation_conditions")
    if isinstance(inv, list):
        parts = [_cap_discord_field(x, max_chars=80) for x in inv if isinstance(x, str) and x.strip()]
        return "; ".join(parts[:3])
    if isinstance(inv, str) and inv.strip():
        return _cap_discord_field(inv, max_chars=120)
    return ""


def _split_decisions_by_type(
    decisions: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    buckets: dict[str, list[dict[str, Any]]] = {
        "BUY": [],
        "WATCH": [],
        "PASS": [],
        "BLOCKED": [],
    }
    for item in decisions:
        if not isinstance(item, dict):
            continue
        sym = _decision_ticker(item)
        if not sym:
            continue
        dec = str(item.get("decision") or "").strip().upper()
        if dec not in buckets:
            continue
        row = dict(item)
        row["ticker"] = sym
        buckets[dec].append(row)
    buckets["BUY"].sort(key=lambda r: (-_decision_cio_score(r), r["ticker"]))
    buckets["WATCH"].sort(key=lambda r: (-_decision_cio_score(r), r["ticker"]))
    buckets["PASS"].sort(key=lambda r: r["ticker"])
    buckets["BLOCKED"].sort(key=lambda r: r["ticker"])
    return buckets


def _market_context_lines(structured: dict[str, Any]) -> list[str]:
    summary = structured.get("summary")
    if not isinstance(summary, dict):
        summary = {}
    regime = summary.get("market_regime")
    tech_bias = summary.get("tech_bias")
    risk = summary.get("overall_risk_level")
    session_message = summary.get("session_message")
    notes = structured.get("notes")

    has_context = any(
        isinstance(v, str) and v.strip()
        for v in (regime, tech_bias, risk, session_message, notes)
    )
    if not has_context:
        return []

    lines = ["📌 **Market Context**", ""]
    if isinstance(regime, str) and regime.strip():
        lines.append(f"**Regime:** {_cap_discord_field(regime, max_chars=120)}")
    if isinstance(tech_bias, str) and tech_bias.strip():
        lines.append(f"**Tech Bias:** {_cap_discord_field(tech_bias, max_chars=120)}")
    if isinstance(risk, str) and risk.strip():
        lines.append(f"**Risk Level:** {_cap_discord_field(risk, max_chars=80)}")
    concise_note = ""
    if isinstance(session_message, str) and session_message.strip():
        concise_note = _cap_discord_field(session_message, max_chars=160)
    elif isinstance(notes, str) and notes.strip():
        concise_note = _cap_discord_field(notes, max_chars=160)
    if concise_note:
        lines.append(f"**Note:** {concise_note}")
    lines.append("")
    return lines


def _format_buy_detail(row: dict[str, Any]) -> list[str]:
    sym = row["ticker"]
    direction = _cap_discord_field(row.get("direction"), max_chars=40)
    strategy = _cap_discord_field(row.get("strategy"), max_chars=40)
    conviction = _cap_discord_field(row.get("conviction"), max_chars=40)
    meta = " · ".join(x for x in (direction, strategy, conviction) if x)
    header = f"**`{sym}`**" + (f" — {meta}" if meta else "")
    lines = [header]

    trade_bits: list[str] = []
    entry = _cap_discord_field(row.get("entry_zone"), max_chars=80)
    stop = _cap_discord_field(row.get("stop_loss"), max_chars=60)
    target = _format_targets_row(row)
    rr = row.get("risk_reward")
    if entry:
        trade_bits.append(f"Entry: {entry}")
    if stop:
        trade_bits.append(f"Stop: {stop}")
    if target:
        trade_bits.append(f"Target: {target}")
    if rr is not None and str(rr).strip():
        trade_bits.append(f"R/R: {_cap_discord_field(rr, max_chars=40)}")
    if trade_bits:
        lines.append("- " + " | ".join(trade_bits))

    thesis = _cap_discord_field(
        row.get("technical_thesis"), max_chars=_CIO_DISCORD_BUY_THESIS_MAX_CHARS
    )
    reason = _cap_discord_field(
        row.get("reason"), max_chars=_CIO_DISCORD_BUY_REASON_MAX_CHARS
    )
    if thesis:
        lines.append(f"- **Thesis:** {thesis}")
    if reason:
        lines.append(f"- **Reason:** {reason}")

    inv = _format_invalidation(row)
    if inv:
        lines.append(f"- **Invalidate:** {inv}")

    action = _cap_discord_field(
        row.get("action_required"), max_chars=_CIO_DISCORD_BUY_ACTION_MAX_CHARS
    )
    if action:
        lines.append(f"- **Action:** {action}")

    return lines


def _format_watch_detail(row: dict[str, Any]) -> list[str]:
    sym = row["ticker"]
    if _is_system_fallback_row(row):
        lines = [f"- **`{sym}`** — WATCH"]
        reason = _cap_discord_field(row.get("reason"), max_chars=160)
        action = _cap_discord_field(row.get("action_required"), max_chars=120)
        if reason:
            lines.append(f"  - **Reason:** {reason}")
        if action:
            lines.append(f"  - **Action:** {action}")
        return lines

    direction = _cap_discord_field(row.get("direction"), max_chars=40)
    strategy = _cap_discord_field(row.get("strategy"), max_chars=40)
    meta = " · ".join(x for x in (direction, strategy) if x)
    header = f"**`{sym}`**" + (f" — {meta}" if meta else "")
    lines = [header]

    reason = _cap_discord_field(row.get("reason"), max_chars=140)
    thesis = _cap_discord_field(row.get("technical_thesis"), max_chars=160)
    if reason:
        lines.append(f"- **Case:** {reason}")
    elif thesis:
        lines.append(f"- **Case:** {thesis}")
    if thesis and reason and thesis != reason:
        lines.append(f"- **Gap:** {thesis}")

    revisit = _cap_discord_field(row.get("revisit_condition"), max_chars=120)
    action = _cap_discord_field(row.get("action_required"), max_chars=120)
    trigger = revisit or action
    if trigger:
        lines.append(f"- **Trigger:** {trigger}")

    inv = _format_invalidation(row)
    if inv:
        lines.append(f"- **Invalidate:** {inv}")

    return lines


def _format_pass_blocked_line(row: dict[str, Any]) -> str:
    sym = row["ticker"]
    dec = str(row.get("decision") or "").strip().upper()
    reason = _cap_discord_field(row.get("reason"), max_chars=120)
    revisit = _cap_discord_field(row.get("revisit_condition"), max_chars=80)
    base = f"- **`{sym}`** — {dec}"
    if reason:
        base += f": {reason}"
    if revisit:
        base += f" _(Revisit: {revisit})_"
    return base


def _fallback_cio_discord_from_decisions(
    structured: dict[str, Any],
    session: str | SessionName,
) -> str:
    """Build rich CIO markdown from final structured output when model markdown is empty."""
    decisions_raw = structured.get("decisions")
    decisions = decisions_raw if isinstance(decisions_raw, list) else []
    buckets = _split_decisions_by_type(decisions)

    session_label = str(session).replace("_", " ").title()
    lines = [f"🧠 **SwingTrader — CIO Decision Brief | {session_label}**", ""]
    lines.extend(_market_context_lines(structured))

    buys = buckets["BUY"]
    lines.append(f"🟢 **BUY Candidates** ({len(buys)})")
    lines.append("")
    if not buys:
        lines.append("_No confirmed BUY candidates this session._")
        lines.append("")
    else:
        shown = buys[:_CIO_DISCORD_MAX_BUY_DETAIL]
        for row in shown:
            lines.extend(_format_buy_detail(row))
            lines.append("")
        if len(buys) > len(shown):
            rest = ", ".join(f"`{r['ticker']}`" for r in buys[len(shown) :])
            lines.append(f"_+{len(buys) - len(shown)} more BUY:_ {rest}")
            lines.append("")

    watches = buckets["WATCH"]
    lines.append(f"🟡 **WATCH Candidates** ({len(watches)})")
    lines.append("")
    if not watches:
        lines.append("_(none)_")
        lines.append("")
    else:
        shown = watches[:_CIO_DISCORD_MAX_WATCH_DETAIL]
        for row in shown:
            lines.extend(_format_watch_detail(row))
            lines.append("")
        if len(watches) > len(shown):
            rest = ", ".join(f"`{r['ticker']}`" for r in watches[len(shown) :])
            lines.append(f"_+{len(watches) - len(shown)} more WATCH:_ {rest}")
            lines.append("")

    pass_rows = buckets["PASS"]
    blocked_rows = buckets["BLOCKED"]
    pb_count = len(pass_rows) + len(blocked_rows)
    lines.append(f"🔴 **PASS / BLOCKED Summary** ({pb_count})")
    lines.append("")
    if pb_count == 0:
        lines.append("_(none)_")
    else:
        for row in pass_rows + blocked_rows:
            lines.append(_format_pass_blocked_line(row))
    lines.append("")

    return "\n".join(lines).strip()


def _fallback_decision_row(ticker: str) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "decision": "WATCH",
        "direction": "Long",
        "conviction": "Low",
        "source": "system_fallback",
        "reason": (
            "CIO omitted ticker from response; preserving CIO-pool candidate for "
            "operator review based on technical/sentiment screening."
        ),
        "action_required": "Manual review required before trade.",
    }


def _complete_cio_decisions_to_pool(
    structured: dict[str, Any],
    cio_symbols: list[str],
) -> tuple[dict[str, Any], list[str]]:
    """Ensure one decision row per CIO pool ticker by appending fallback WATCH rows."""
    decisions_raw = structured.get("decisions")
    decisions = decisions_raw if isinstance(decisions_raw, list) else []
    final: list[dict[str, Any]] = []
    by_symbol: dict[str, dict[str, Any]] = {}
    for item in decisions:
        if not isinstance(item, dict):
            continue
        sym = _decision_ticker(item)
        if not sym:
            continue
        row = dict(item)
        row["ticker"] = sym
        by_symbol[sym] = row

    ordered_pool = [s.strip().upper() for s in cio_symbols if isinstance(s, str) and s.strip()]
    missing: list[str] = []
    for sym in ordered_pool:
        row = by_symbol.get(sym)
        if row is None:
            missing.append(sym)
            final.append(_fallback_decision_row(sym))
            continue
        final.append(row)
    return {**structured, "decisions": final}, missing


def _decision_buckets(decisions_raw: Any) -> tuple[int, int, int, int]:
    buy = watch = passed = blocked = 0
    if not isinstance(decisions_raw, list):
        return buy, watch, passed, blocked
    for item in decisions_raw:
        if not isinstance(item, dict):
            continue
        dec = str(item.get("decision") or "").strip().upper()
        if dec == "BUY":
            buy += 1
        elif dec == "WATCH":
            watch += 1
        elif dec == "PASS":
            passed += 1
        elif dec == "BLOCKED":
            blocked += 1
    return buy, watch, passed, blocked


def _highest_buy_ticker(decisions_raw: Any) -> str | None:
    if not isinstance(decisions_raw, list):
        return None
    buys: list[dict[str, Any]] = []
    for item in decisions_raw:
        if not isinstance(item, dict):
            continue
        if str(item.get("decision") or "").strip().upper() != "BUY":
            continue
        sym = _decision_ticker(item)
        if not sym:
            continue
        row = dict(item)
        row["ticker"] = sym
        buys.append(row)
    if not buys:
        return None

    def _score(x: dict[str, Any]) -> float:
        raw = x.get("cio_score")
        if isinstance(raw, (int, float)):
            return float(raw)
        if isinstance(raw, str):
            try:
                return float(raw.strip())
            except ValueError:
                return float("-inf")
        return float("-inf")

    best = max(buys, key=_score)
    best_score = _score(best)
    if best_score == float("-inf"):
        return str(best.get("ticker"))
    return str(best.get("ticker"))


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
    decisions = structured.get("decisions")
    decision_count = _count_cio_decisions(structured)
    buy, watch, passed, blocked = _decision_buckets(decisions)
    if decision_count > 0:
        buy_count, watch_count, pass_count, blocked_count = buy, watch, passed, blocked
    else:
        buy_count = _summary_int(summary, "buy_count")
        watch_count = _summary_int(summary, "watch_count")
        pass_count = _summary_int(summary, "pass_count")
        blocked_count = _summary_int(summary, "blocked_count")

    notes = structured.get("notes")
    notes_text = notes.strip() if isinstance(notes, str) else ""
    session_message = summary.get("session_message")
    session_message_text = (
        session_message.strip() if isinstance(session_message, str) else ""
    )
    risk_notes = notes_text or session_message_text or "_No risk notes._"

    if decision_count > 0:
        highest_display = _highest_buy_ticker(decisions) or "None"
    else:
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
        f"BUY: {buy_count}\n"
        f"WATCH: {watch_count}\n"
        f"PASS: {pass_count}\n"
        f"BLOCKED: {blocked_count}\n\n"
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
        max_tokens=CIO_MAX_OUTPUT_TOKENS,
        call_label="cio",
    )
    structured, _shape = _normalize_cio_structured(raw)
    raw_decision_count = _count_cio_decisions(structured)
    structured = _filter_cio_decisions_to_pool(structured, cio_symbols)
    structured, missing = _complete_cio_decisions_to_pool(structured, cio_symbols)
    if missing:
        logger.warning(
            "CIO completion added %s fallback decision(s) for missing tickers: %s",
            len(missing),
            ", ".join(missing),
        )
    decision_count = _count_cio_decisions(structured)
    logger.info(
        "CIO final decision count after completion: %s/%s",
        decision_count,
        len(cio_symbols),
    )

    md = _resolve_cio_discord_markdown(raw, structured)
    if not md and isinstance(structured.get("decisions"), list) and structured["decisions"]:
        logger.warning("CIO markdown missing; built fallback markdown from decisions")
        md = _fallback_cio_discord_from_decisions(structured, ctx.session)
    md = md or "_No CIO output_"

    logger.info(
        "CIO decisions returned: %s raw -> %s final (pool=%s)",
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
