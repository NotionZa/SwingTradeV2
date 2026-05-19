from __future__ import annotations

import logging
from typing import Any

from anthropic import Anthropic

from swingtrade.integrations.anthropic_client import complete_json_agent
from swingtrade.integrations.ta_features import compute_ta_features
from swingtrade.integrations.yfinance_data import (
    fetch_ohlcv,
    fetch_yfinance_market_cap_usd,
    ohlcv_for_ticker,
)
from swingtrade.models.agents import AgentResult, RunContext
from swingtrade.prompt_loader import load_system_prompt
from swingtrade.settings import Settings

logger = logging.getLogger(__name__)

_NO_CLEAN_SETUP = "No Clean Setup"
_NO_TRADE = "No Trade"
_BROKEN_TREND = "Broken"
_UNDERPERFORMING_RS = "Underperforming"
_BREAKOUT = "Breakout"
_SETUP_A = "A"

_CAP_RR_BELOW_25 = 6.9
_CAP_NO_CLEAN_OR_NO_TRADE_OR_BROKEN = 4.9
_CAP_UNDERPERFORMING_RS = 6.5
_CAP_INCOMPLETE_LEVELS = 5.9
_CAP_PREMARKET_WEAK_VOLUME = 7.4

_PREMARKET_WEAK_VOLUME_PHRASES = (
    "pre-market",
    "premarket",
    "low volume",
    "thin volume",
    "below confirmation",
    "volume ratio below",
    "volume <",
)


def _format_usd_compact(n: float) -> str:
    sign = "-" if n < 0 else ""
    x = abs(float(n))
    if x >= 1e12:
        return f"{sign}${x / 1e12:.2f}T"
    if x >= 1e9:
        return f"{sign}${x / 1e9:.2f}B"
    if x >= 1e6:
        return f"{sign}${x / 1e6:.2f}M"
    if x >= 1e3:
        return f"{sign}${x / 1e3:.2f}K"
    return f"{sign}${x:,.0f}"


def _session_label(session: str) -> str:
    return str(session).replace("_", " ").title()


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _row_str(row: dict[str, Any], key: str) -> str | None:
    v = row.get(key)
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def _level_field_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def _row_text_blob(row: dict[str, Any]) -> str:
    parts: list[str] = []
    vc = row.get("volume_confirmation")
    if isinstance(vc, str) and vc.strip():
        parts.append(vc)
    risks = row.get("technical_risks")
    if isinstance(risks, list):
        parts.extend(str(r) for r in risks if r is not None)
    for key in ("summary", "cio_notes"):
        t = row.get(key)
        if isinstance(t, str) and t.strip():
            parts.append(t)
    return " ".join(parts).lower()


def _weak_premarket_volume(row: dict[str, Any], session: str | None) -> bool:
    if session != "pre_market":
        return False
    blob = _row_text_blob(row)
    return any(phrase in blob for phrase in _PREMARKET_WEAK_VOLUME_PHRASES)


def _cap_score(score: float, cap: float, reasons: list[str], reason: str) -> float:
    if score > cap:
        reasons.append(reason)
        return cap
    return score


def _apply_ta_score_caps_to_row(
    row: dict[str, Any],
    session: str | None = None,
) -> dict[str, Any]:
    """Apply deterministic downward TA score caps; never raises scores."""
    out = dict(row)
    original = _as_float(out.get("ta_score"))
    if original is None:
        return out

    score = original
    reasons: list[str] = []

    rr = _as_float(out.get("risk_reward"))
    if rr is not None and rr < 2.5:
        score = _cap_score(
            score,
            _CAP_RR_BELOW_25,
            reasons,
            f"Risk/reward below 2.5 capped TA score at {_CAP_RR_BELOW_25}",
        )

    if (_row_str(out, "strategy_match") or "") == _NO_CLEAN_SETUP:
        score = _cap_score(
            score,
            _CAP_NO_CLEAN_OR_NO_TRADE_OR_BROKEN,
            reasons,
            f"No Clean Setup capped TA score at {_CAP_NO_CLEAN_OR_NO_TRADE_OR_BROKEN}",
        )

    if (_row_str(out, "setup_quality") or "") == _NO_TRADE:
        score = _cap_score(
            score,
            _CAP_NO_CLEAN_OR_NO_TRADE_OR_BROKEN,
            reasons,
            f"No Trade setup quality capped TA score at {_CAP_NO_CLEAN_OR_NO_TRADE_OR_BROKEN}",
        )

    if (_row_str(out, "trend_status") or "") == _BROKEN_TREND:
        score = _cap_score(
            score,
            _CAP_NO_CLEAN_OR_NO_TRADE_OR_BROKEN,
            reasons,
            f"Broken trend capped TA score at {_CAP_NO_CLEAN_OR_NO_TRADE_OR_BROKEN}",
        )

    rs = _row_str(out, "relative_strength_vs_qqq") or ""
    strat = _row_str(out, "strategy_match") or ""
    qual = _row_str(out, "setup_quality") or ""
    if rs == _UNDERPERFORMING_RS and not (strat == _BREAKOUT and qual == _SETUP_A):
        score = _cap_score(
            score,
            _CAP_UNDERPERFORMING_RS,
            reasons,
            f"Underperforming vs QQQ capped TA score at {_CAP_UNDERPERFORMING_RS}",
        )

    if (
        _level_field_missing(out.get("suggested_entry_zone"))
        or _level_field_missing(out.get("suggested_stop_loss"))
        or _level_field_missing(out.get("suggested_target"))
    ):
        score = _cap_score(
            score,
            _CAP_INCOMPLETE_LEVELS,
            reasons,
            f"Incomplete entry/stop/target capped TA score at {_CAP_INCOMPLETE_LEVELS}",
        )

    if _weak_premarket_volume(out, session):
        score = _cap_score(
            score,
            _CAP_PREMARKET_WEAK_VOLUME,
            reasons,
            f"Pre-market weak volume capped TA score at {_CAP_PREMARKET_WEAK_VOLUME}",
        )

    score = round(score, 1)
    original_r = round(original, 1)

    if score != original_r:
        out["model_ta_score"] = original_r
        out["score_cap_reasons"] = reasons
    elif "score_cap_reasons" not in out:
        out["score_cap_reasons"] = []

    out["ta_score"] = score
    return out


def _apply_ta_score_caps(
    structured: dict[str, Any],
    session: str | None = None,
) -> dict[str, Any]:
    """Cap all structured.tickers rows and sync structured.scores."""
    if not isinstance(structured, dict):
        return structured

    tickers_in = structured.get("tickers")
    tickers: dict[str, Any] = {}
    if isinstance(tickers_in, dict):
        for sym, row in tickers_in.items():
            if isinstance(row, dict):
                tickers[str(sym)] = _apply_ta_score_caps_to_row(row, session)
            else:
                tickers[str(sym)] = row

    scores: dict[str, Any] = {}
    existing_scores = structured.get("scores")
    if isinstance(existing_scores, dict):
        scores.update(existing_scores)

    for sym, row in tickers.items():
        if not isinstance(row, dict):
            continue
        key = _row_str(row, "ticker") or sym
        capped = _as_float(row.get("ta_score"))
        if capped is not None:
            scores[key] = round(capped, 1)

    return {**structured, "tickers": tickers, "scores": scores}


def _normalize_ticker_rows(structured: dict[str, Any]) -> list[tuple[str, dict[str, Any], float | None]]:
    tickers = structured.get("tickers")
    if not isinstance(tickers, dict) or not tickers:
        return []
    out: list[tuple[str, dict[str, Any], float | None]] = []
    for sym, row in tickers.items():
        if not isinstance(row, dict):
            continue
        symbol = _row_str(row, "ticker") or str(sym).strip()
        if not symbol:
            continue
        out.append((symbol, row, _as_float(row.get("ta_score"))))
    return out


def _is_no_clean_setup_row(row: dict[str, Any], score: float | None) -> bool:
    setup = _row_str(row, "strategy_match") or ""
    quality = _row_str(row, "setup_quality") or ""
    if setup == _NO_CLEAN_SETUP:
        return True
    if quality == _NO_TRADE:
        return True
    if score is not None and score < 5.0:
        return True
    return False


def _is_strongest_candidate(row: dict[str, Any], score: float | None) -> bool:
    if score is None or score < 7.0:
        return False
    setup = _row_str(row, "strategy_match") or ""
    quality = _row_str(row, "setup_quality") or ""
    if setup == _NO_CLEAN_SETUP:
        return False
    if quality == _NO_TRADE:
        return False
    return True


def _is_watchlist_candidate(row: dict[str, Any], score: float | None) -> bool:
    if _is_no_clean_setup_row(row, score):
        return False
    quality = (_row_str(row, "setup_quality") or "").upper()
    if quality in ("B", "C"):
        return True
    if score is not None and 5.0 <= score <= 6.9:
        return True
    if score is not None and score >= 5.0:
        return True
    return False


def _levels_line(row: dict[str, Any]) -> str | None:
    ks, kr = row.get("key_support"), row.get("key_resistance")
    parts: list[str] = []
    if ks is not None and str(ks).strip():
        parts.append(f"Support: {ks}")
    if kr is not None and str(kr).strip():
        parts.append(f"Resistance: {kr}")
    return " | ".join(parts) if parts else None


def _trade_plan_line(row: dict[str, Any]) -> str | None:
    parts: list[str] = []
    entry = _row_str(row, "suggested_entry_zone")
    stop = row.get("suggested_stop_loss")
    target = row.get("suggested_target")
    rr = row.get("risk_reward")
    if entry:
        parts.append(f"Entry: {entry}")
    if stop is not None and str(stop).strip():
        parts.append(f"Stop: {stop}")
    if target is not None and str(target).strip():
        parts.append(f"Target: {target}")
    if rr is not None and str(rr).strip():
        parts.append(f"R/R: {rr}")
    return " | ".join(parts) if parts else None


def _risks_line(row: dict[str, Any]) -> str | None:
    risks = row.get("technical_risks")
    if isinstance(risks, list):
        items = [str(r).strip() for r in risks if r is not None and str(r).strip()]
        if items:
            return "Risks: " + "; ".join(items[:2])
    summary = _row_str(row, "summary")
    if summary:
        return f"Note: {summary[:120]}"
    return None


def _format_ticker_block(symbol: str, row: dict[str, Any]) -> list[str]:
    """Grouped bullet block for one ticker (no markdown tables)."""
    score = _as_float(row.get("ta_score"))
    score_s = f"{score:.1f}" if score is not None else "N/A"
    setup = _row_str(row, "strategy_match") or "N/A"
    quality = _row_str(row, "setup_quality") or "N/A"
    cap = _row_str(row, "market_cap_human") or "N/A"
    trend = _row_str(row, "trend_status") or "N/A"
    mom = _row_str(row, "momentum_status") or "N/A"
    rs = _row_str(row, "relative_strength_vs_qqq") or "N/A"

    lines = [
        f"**{symbol}**",
        f"- Setup: {setup} | TA: {score_s} | Quality: {quality}",
        f"- Mkt cap: {cap}",
        f"- Trend: {trend} | Momentum: {mom} | RS vs QQQ: {rs}",
    ]
    levels = _levels_line(row)
    if levels:
        lines.append(f"- {levels}")
    plan = _trade_plan_line(row)
    if plan:
        lines.append(f"- {plan}")
    risk = _risks_line(row)
    if risk:
        lines.append(f"- {risk}")
    return lines


def _partition_ticker_rows(
    rows: list[tuple[str, dict[str, Any], float | None]],
) -> tuple[
    list[tuple[str, dict[str, Any], float | None]],
    list[tuple[str, dict[str, Any], float | None]],
    list[tuple[str, dict[str, Any], float | None]],
]:
    strongest_pool = [
        item for item in rows if _is_strongest_candidate(item[1], item[2])
    ]
    strongest_pool.sort(key=lambda x: (x[2] if x[2] is not None else -1.0), reverse=True)
    strongest = strongest_pool[:3]
    strongest_syms = {s for s, _, _ in strongest}

    remaining = [item for item in rows if item[0] not in strongest_syms]

    no_clean: list[tuple[str, dict[str, Any], float | None]] = []
    watchlist: list[tuple[str, dict[str, Any], float | None]] = []

    for item in remaining:
        sym, row, score = item
        if _is_no_clean_setup_row(row, score):
            no_clean.append(item)
        elif _is_watchlist_candidate(row, score):
            watchlist.append(item)
        else:
            no_clean.append(item)

    watchlist.sort(key=lambda x: (x[2] if x[2] is not None else -1.0), reverse=True)
    no_clean.sort(key=lambda x: (x[2] if x[2] is not None else -1.0), reverse=True)
    return strongest, watchlist, no_clean


def _section_blocks(
    title: str,
    items: list[tuple[str, dict[str, Any], float | None]],
    *,
    empty_line: str,
) -> list[str]:
    lines = [title, ""]
    if not items:
        lines.append(empty_line)
        lines.append("")
        return lines
    for sym, row, _ in items:
        lines.extend(_format_ticker_block(sym, row))
        lines.append("")
    return lines


def _format_ta_discord_grouped_from_structured(structured: dict[str, Any], session: str) -> str:
    """Deterministic grouped-bullet watchlist post from structured.tickers."""
    rows = _normalize_ticker_rows(structured)
    if not rows:
        return ""

    strongest, watchlist, no_clean = _partition_ticker_rows(rows)
    session_l = _session_label(session)

    lines = [
        f"⚡ **SwingTrader — Technical Analysis | {session_l}**",
        "",
        "**Session Note:** Watchlist formatted from structured ticker analysis (Python builder).",
        "",
    ]
    lines.extend(
        _section_blocks(
            "🟢 **Strongest Setups**",
            strongest,
            empty_line="_None in this bucket._",
        )
    )
    lines.extend(
        _section_blocks(
            "🟡 **Watchlist / Needs Confirmation**",
            watchlist,
            empty_line="_None in this bucket._",
        )
    )
    lines.extend(
        _section_blocks(
            "🔴 **No Clean Setup**",
            no_clean,
            empty_line="_None in this bucket._",
        )
    )

    notes = structured.get("notes")
    lines.append("📝 **Cross-Cutting Notes**")
    lines.append("")
    if isinstance(notes, str) and notes.strip():
        lines.append(notes.strip())
    else:
        lines.append("_No cross-cutting notes._")

    return "\n".join(lines).strip()


def _features_usable(feats: Any) -> bool:
    if not isinstance(feats, dict) or not feats:
        return False
    if feats.get("error"):
        return False
    return feats.get("last_close") is not None


def _rough_feature_score(feats: dict[str, Any]) -> float | None:
    """Heuristic 0–10 screen from local indicators (not model TA score)."""
    if not _features_usable(feats):
        return None
    score = 5.0
    rsi = _as_float(feats.get("rsi_14"))
    if rsi is not None:
        if 50.0 <= rsi <= 70.0:
            score += 1.0
        elif rsi > 70.0:
            score += 0.5
        elif rsi < 30.0:
            score += 0.5
    macd = _as_float(feats.get("macd"))
    macd_sig = _as_float(feats.get("macd_signal"))
    if macd is not None and macd_sig is not None and macd > macd_sig:
        score += 0.5
    last = _as_float(feats.get("last_close"))
    bb_mid = _as_float(feats.get("bb_mid"))
    if last is not None and bb_mid is not None and last > bb_mid:
        score += 0.5
    vol_ratio = _as_float(feats.get("volume_ratio"))
    if vol_ratio is not None and vol_ratio >= 1.0:
        score += 0.3
    return min(score, 10.0)


def _feature_risk_note(feats: dict[str, Any]) -> str:
    notes: list[str] = []
    rsi = _as_float(feats.get("rsi_14"))
    if rsi is not None and rsi > 70.0:
        notes.append("RSI extended")
    if rsi is not None and rsi < 30.0:
        notes.append("RSI oversold")
    vol_ratio = _as_float(feats.get("volume_ratio"))
    if vol_ratio is not None and vol_ratio < 0.8:
        notes.append("weak volume")
    last = _as_float(feats.get("last_close"))
    bb_upper = _as_float(feats.get("bb_upper"))
    if last is not None and bb_upper is not None and last >= bb_upper * 0.99:
        notes.append("near upper Bollinger")
    if not notes:
        return "Basic feature fallback only — no model-derived setup classification."
    return "; ".join(notes)


def _format_feature_ticker_block(sym: str, block: dict[str, Any], rough: float | None) -> list[str]:
    feats = block.get("features") if isinstance(block.get("features"), dict) else {}
    yfq = block.get("yfinance_quote") if isinstance(block.get("yfinance_quote"), dict) else {}
    score_s = f"{rough:.1f}" if rough is not None else "N/A"

    cap = "N/A"
    mc = yfq.get("market_cap_usd")
    if mc is not None:
        try:
            cap = _format_usd_compact(float(mc))
        except (TypeError, ValueError):
            pass

    last = _as_float(feats.get("last_close"))
    last_s = f"{last:.2f}" if last is not None else "N/A"

    lines = [
        f"**{sym}**",
        f"- Screen score (local): {score_s} | Mkt cap: {cap} | Last: {last_s}",
    ]

    rsi = _as_float(feats.get("rsi_14"))
    if rsi is not None:
        lines.append(f"- RSI(14): {rsi:.1f}")

    macd = _as_float(feats.get("macd"))
    macd_sig = _as_float(feats.get("macd_signal"))
    if macd is not None and macd_sig is not None:
        lines.append(f"- MACD: {macd:.3f} | Signal: {macd_sig:.3f}")

    bb_mid = _as_float(feats.get("bb_mid"))
    bb_upper = _as_float(feats.get("bb_upper"))
    bb_lower = _as_float(feats.get("bb_lower"))
    if last is not None and bb_mid is not None:
        bb_parts = [f"mid {bb_mid:.2f}"]
        if bb_upper is not None:
            bb_parts.append(f"upper {bb_upper:.2f}")
        if bb_lower is not None:
            bb_parts.append(f"lower {bb_lower:.2f}")
        lines.append(f"- vs Bollinger: last {last:.2f} | " + " / ".join(bb_parts))

    vol_ratio = _as_float(feats.get("volume_ratio"))
    if vol_ratio is not None:
        lines.append(f"- Volume ratio (20d): {vol_ratio:.2f}x")

    rel = _as_float(block.get("vs_qqq_close_ratio"))
    if rel is not None:
        lines.append(f"- vs QQQ close ratio: {rel:.4f}")

    lines.append(f"- {_feature_risk_note(feats)}")
    return lines


def _format_ta_discord_from_features(
    per: dict[str, Any],
    session: str | None = None,
) -> str:
    """Last-resort watchlist from locally computed per-ticker features."""
    if not isinstance(per, dict) or not per:
        return ""

    ranked: list[tuple[str, dict[str, Any], float | None]] = []
    for sym, block in per.items():
        if not isinstance(block, dict):
            continue
        feats = block.get("features")
        if not _features_usable(feats):
            continue
        ranked.append((str(sym).strip(), block, _rough_feature_score(feats)))

    if not ranked:
        return ""

    ranked.sort(
        key=lambda x: (x[2] if x[2] is not None else -1.0),
        reverse=True,
    )
    session_l = _session_label(session or "unknown")

    lines = [
        f"⚡ **SwingTrader — Technical Analysis | {session_l}**",
        "",
        "**Session Note:** Fallback generated from local technical features because "
        "model markdown/structured output was unavailable. Treat as a basic technical screen.",
        "",
        "🟢 **Top Technical Scores**",
        "",
    ]
    for sym, block, rough in ranked:
        lines.extend(_format_feature_ticker_block(sym, block, rough))
        lines.append("")

    lines.append(
        "_Basic feature fallback only — no model-derived setup classification._"
    )
    return "\n".join(lines).strip()


def _format_ta_discord_scores_only(structured: dict[str, Any], session: str) -> str:
    """Score-only fallback when structured.tickers is missing."""
    scores = structured.get("scores")
    if not isinstance(scores, dict) or not scores:
        return ""

    ranked: list[tuple[str, float]] = []
    for sym, val in scores.items():
        score = _as_float(val)
        if score is None:
            continue
        ranked.append((str(sym).strip(), score))
    if not ranked:
        return ""

    ranked.sort(key=lambda x: x[1], reverse=True)
    session_l = _session_label(session)

    lines = [
        f"⚡ **SwingTrader — Technical Analysis | {session_l}**",
        "",
        "**Session Note:** Full ticker structure was unavailable, but score output was returned.",
        "",
        "🟢 **Top TA Scores**",
        "",
    ]
    for sym, score in ranked[:15]:
        lines.append(f"- {sym} — {score:.1f}")
    lines.append("")
    lines.append("📝 **Cross-Cutting Notes**")
    lines.append("")
    notes = structured.get("notes")
    if isinstance(notes, str) and notes.strip():
        lines.append(notes.strip())
    else:
        lines.append("_No cross-cutting notes._")
    lines.extend(
        [
            "",
            "_No detailed levels available. Re-run with fewer tickers if full setup detail is needed._",
        ]
    )
    return "\n".join(lines).strip()


def _flat_scores_from_raw(raw: dict[str, Any]) -> dict[str, Any]:
    """Some model replies put ticker scores at top level instead of structured.scores."""
    out: dict[str, Any] = {}
    for key, val in raw.items():
        if key in ("discord_markdown", "structured"):
            continue
        if _as_float(val) is not None:
            out[str(key)] = val
    return out


def _resolve_technical_discord_markdown(
    raw: dict[str, Any],
    session: str | None = None,
    per: dict[str, Any] | None = None,
) -> str:
    """Resolve Discord markdown: structured.tickers (Python) first, then model markdown, then fallbacks."""
    session_key = session or "unknown"
    structured = raw.get("structured")
    if not isinstance(structured, dict):
        structured = {}

    grouped = _format_ta_discord_grouped_from_structured(structured, session_key)
    if grouped:
        logger.info(
            "Technical agent: watchlist markdown built from structured.tickers (Python formatter)"
        )
        return grouped

    nested_md = structured.get("discord_markdown")
    if isinstance(nested_md, str) and nested_md.strip():
        logger.warning(
            "Technical agent: using structured.discord_markdown (structured.tickers unavailable)"
        )
        return nested_md.strip()

    raw_md = str(raw.get("discord_markdown", "")).strip()
    if raw_md:
        logger.warning(
            "Technical agent: using raw discord_markdown (structured.tickers unavailable)"
        )
        return raw_md

    if not isinstance(structured.get("scores"), dict) or not structured.get("scores"):
        flat = _flat_scores_from_raw(raw)
        if flat:
            structured = {**structured, "scores": flat}

    scores_only = _format_ta_discord_scores_only(structured, session_key)
    if scores_only:
        logger.warning(
            "Technical agent: built score-only watchlist from structured.scores"
        )
        return scores_only

    features_md = _format_ta_discord_from_features(per or {}, session)
    if features_md:
        logger.warning(
            "Technical agent: built watchlist from local per-ticker features (model output unusable)"
        )
        return features_md

    logger.warning(
        "Technical agent: no structured.tickers, model markdown, scores, or local features to format"
    )
    return ""


def _discord_market_cap_snapshot(per: dict[str, Any]) -> str:
    lines = [
        "",
        "### Market cap (USD, Yahoo Finance)",
        "_Quote-derived (session-aware); not audited filing float._",
        "",
    ]
    for sym in sorted(per):
        q = per[sym].get("yfinance_quote") if isinstance(per[sym], dict) else None
        mc = None
        if isinstance(q, dict):
            mc = q.get("market_cap_usd")
        if mc is None:
            lines.append(f"- **{sym}**: _n/a_")
        else:
            lines.append(f"- **{sym}**: {_format_usd_compact(float(mc))}")
    return "\n".join(lines)


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
        yfq = fetch_yfinance_market_cap_usd(sym)
        per[sym] = {
            "features": feats,
            "vs_qqq_close_ratio": rel,
            "yfinance_quote": yfq,
        }

    user = f"Session={ctx.session}\nPer-ticker features:\n{per}"
    raw = complete_json_agent(
        client,
        model=settings.anthropic_model_sonnet,
        system=load_system_prompt("technical"),
        user=user,
        max_tokens=8192,
        timeout_seconds=300.0,
    )

    structured = raw.get("structured")
    if not isinstance(structured, dict):
        structured = {"scores": {}, "notes": ""}
    structured = _apply_ta_score_caps(structured, ctx.session)
    raw = {**raw, "structured": structured}

    md = (
        _resolve_technical_discord_markdown(raw, ctx.session, per) or "_No TA output_"
    ).rstrip()
    structured = {**structured, "inputs": per}
    return AgentResult(
        agent_id="technical_analysis",
        discord_markdown=md,
        structured=structured,
        model_used=settings.anthropic_model_sonnet,
    )
