from __future__ import annotations

import json
from typing import Any, Literal

from swingtrade.candidate_ranker import rank_analysis_pool

SessionName = Literal["pre_market", "post_market"]

# Local size gates (no API calls).
CIO_MESSAGE_IDEAL_CHARS = 25_000
CIO_MESSAGE_ACCEPTABLE_CHARS = 35_000
CIO_MESSAGE_FAIL_CHARS = 40_000

_CIO_MARKET_SUMMARY_MAX = 400
_CIO_TECH_SUMMARY_MAX = 220
_CIO_SENTIMENT_CATALYST_MAX = 180
_CIO_RISK_REASON_MAX = 100
_CIO_RISKS_MAX_ITEMS = 5
_CIO_HARD_VETO_MAX_ITEMS = 30

_CANDIDATE_FIELDS = (
    "ticker",
    "ta_score",
    "sentiment_score",
    "strategy_match",
    "setup_quality",
    "trend_status",
    "momentum_status",
    "relative_strength_vs_qqq",
    "suggested_entry_zone",
    "suggested_stop_loss",
    "suggested_target",
    "risk_reward",
    "summary",
    "technical_risks",
    "sentiment_catalyst",
    "rank_score",
    "analysis_rank",
)


def _cap_text(value: Any, max_chars: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    if len(value) <= max_chars:
        return value
    return value[:max_chars].rstrip() + " ...[truncated]"


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


def _norm_symbols(symbols: list[str]) -> list[str]:
    return [s.strip().upper() for s in symbols if s and str(s).strip()]


def _technical_row(
    prior_structured: dict[str, dict[str, Any]], symbol: str
) -> dict[str, Any] | None:
    ta = prior_structured.get("technical_analysis")
    if not isinstance(ta, dict):
        return None
    tickers = ta.get("tickers")
    if not isinstance(tickers, dict):
        return None
    row = tickers.get(symbol) or tickers.get(symbol.upper())
    return row if isinstance(row, dict) else None


def _sentiment_row(
    prior_structured: dict[str, dict[str, Any]], symbol: str
) -> dict[str, Any] | None:
    se = prior_structured.get("sentiment")
    if not isinstance(se, dict):
        return None
    per = se.get("per_ticker")
    if not isinstance(per, dict):
        return None
    row = per.get(symbol) or per.get(symbol.upper())
    return row if isinstance(row, dict) else None


def market_context_summary(
    prior_structured: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    ms = prior_structured.get("market_sentiment")
    if not isinstance(ms, dict):
        return {}
    out = {
        "regime": ms.get("regime"),
        "trading_bias": ms.get("trading_bias"),
        "confidence_0_10": ms.get("confidence_0_10"),
        "macro_summary": _cap_text(ms.get("macro_summary"), _CIO_MARKET_SUMMARY_MAX),
    }
    return {k: v for k, v in out.items() if v not in (None, "", [])}


def hard_veto_summary(
    prior_structured: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Killed tickers and reasons only — no full veto scan blob."""
    hv = prior_structured.get("hard_veto")
    if not isinstance(hv, dict):
        return {"killed_count": 0, "killed_tickers": []}
    killed: list[dict[str, Any]] = []
    vetoes = hv.get("vetoes")
    if isinstance(vetoes, list):
        for row in vetoes:
            if not isinstance(row, dict) or not row.get("killed"):
                continue
            sym = str(row.get("symbol", "")).strip().upper()
            if not sym:
                continue
            reasons = row.get("reasons")
            if isinstance(reasons, list):
                reason_list = [
                    _cap_text(r, _CIO_RISK_REASON_MAX)
                    for r in reasons[:_CIO_RISKS_MAX_ITEMS]
                    if r is not None and str(r).strip()
                ]
            else:
                reason_list = []
            killed.append({"ticker": sym, "reasons": reason_list})
            if len(killed) >= _CIO_HARD_VETO_MAX_ITEMS:
                break
    return {"killed_count": len(killed), "killed_tickers": killed}


def candidate_rows_for_cio(
    prior_structured: dict[str, dict[str, Any]],
    cio_symbols: list[str],
    analysis_symbols: list[str],
) -> list[dict[str, Any]]:
    ranked = rank_analysis_pool(prior_structured, analysis_symbols)
    rank_by_symbol = {sym: (idx + 1, score) for idx, (sym, score) in enumerate(ranked)}

    out: list[dict[str, Any]] = []
    for sym in _norm_symbols(cio_symbols):
        ta = _technical_row(prior_structured, sym) or {}
        se = _sentiment_row(prior_structured, sym) or {}
        risks = ta.get("technical_risks")
        risk_items = risks if isinstance(risks, list) else []
        rank_idx, rank_score = rank_by_symbol.get(sym, (None, 0.0))
        sent_raw = _as_float(se.get("score_0_10"))

        row: dict[str, Any] = {
            "ticker": sym,
            "ta_score": ta.get("ta_score"),
            "sentiment_score": round(sent_raw, 2) if sent_raw is not None else None,
            "strategy_match": ta.get("strategy_match"),
            "setup_quality": ta.get("setup_quality"),
            "trend_status": ta.get("trend_status"),
            "momentum_status": ta.get("momentum_status"),
            "relative_strength_vs_qqq": ta.get("relative_strength_vs_qqq"),
            "suggested_entry_zone": ta.get("suggested_entry_zone"),
            "suggested_stop_loss": ta.get("suggested_stop_loss"),
            "suggested_target": ta.get("suggested_target"),
            "risk_reward": ta.get("risk_reward"),
            "summary": _cap_text(ta.get("summary"), _CIO_TECH_SUMMARY_MAX),
            "technical_risks": [
                _cap_text(item, _CIO_RISK_REASON_MAX)
                for item in risk_items[:_CIO_RISKS_MAX_ITEMS]
                if isinstance(item, str) and item.strip()
            ],
            "sentiment_catalyst": _cap_text(
                se.get("catalyst"), _CIO_SENTIMENT_CATALYST_MAX
            ),
            "rank_score": round(float(rank_score), 4)
            if isinstance(rank_score, (int, float))
            else 0.0,
            "analysis_rank": rank_idx,
        }
        out.append(row)
    return out


def build_cio_packet(
    prior_structured: dict[str, dict[str, Any]],
    session: SessionName,
    *,
    cio_symbols: list[str],
    analysis_symbols: list[str] | None = None,
) -> dict[str, Any]:
    """Purpose-built compact CIO decision packet (does not mutate *prior_structured*)."""
    cio = _norm_symbols(cio_symbols)
    analysis = _norm_symbols(analysis_symbols if analysis_symbols is not None else cio)
    return {
        "session": session,
        "analysis_universe_count": len(analysis),
        "cio_review_tickers": cio,
        "market_context": market_context_summary(prior_structured),
        "hard_veto_summary": hard_veto_summary(prior_structured),
        "candidates": candidate_rows_for_cio(prior_structured, cio, analysis),
    }


def packet_section_char_counts(packet: dict[str, Any]) -> dict[str, int]:
    sections = (
        "session",
        "analysis_universe_count",
        "cio_review_tickers",
        "market_context",
        "hard_veto_summary",
        "candidates",
    )
    out: dict[str, int] = {}
    for key in sections:
        out[key] = len(json.dumps(packet.get(key), ensure_ascii=False, default=str))
    out["total_packet"] = len(json.dumps(packet, ensure_ascii=False, default=str))
    return out


def format_cio_user_message(packet: dict[str, Any]) -> str:
    return (
        "Session payload for CIO decisioning:\n"
        f"{json.dumps(packet, indent=2, ensure_ascii=False, default=str)}"
    )


def diagnose_cio_packet(
    prior_structured: dict[str, dict[str, Any]],
    session: SessionName,
    *,
    cio_symbols: list[str],
    analysis_symbols: list[str] | None = None,
) -> dict[str, Any]:
    """Local-only size diagnostics; no API calls."""
    packet = build_cio_packet(
        prior_structured,
        session,
        cio_symbols=cio_symbols,
        analysis_symbols=analysis_symbols,
    )
    user_message = format_cio_user_message(packet)
    msg_chars = len(user_message)
    sections = packet_section_char_counts(packet)

    if msg_chars <= CIO_MESSAGE_IDEAL_CHARS:
        size_band = "ideal"
    elif msg_chars <= CIO_MESSAGE_ACCEPTABLE_CHARS:
        size_band = "acceptable"
    elif msg_chars <= CIO_MESSAGE_FAIL_CHARS:
        size_band = "over_acceptable"
    else:
        size_band = "fail"

    return {
        "cio_user_message_chars": msg_chars,
        "cio_candidate_count": len(_norm_symbols(cio_symbols)),
        "compact_packet_candidate_count": len(packet.get("candidates", [])),
        "section_chars": sections,
        "size_band": size_band,
        "within_ideal": msg_chars <= CIO_MESSAGE_IDEAL_CHARS,
        "within_acceptable": msg_chars <= CIO_MESSAGE_ACCEPTABLE_CHARS,
        "within_fail_gate": msg_chars <= CIO_MESSAGE_FAIL_CHARS,
    }


def assert_local_size_gate(
    prior_structured: dict[str, dict[str, Any]],
    session: SessionName,
    *,
    cio_symbols: list[str],
    analysis_symbols: list[str] | None = None,
) -> dict[str, Any]:
    """Raise AssertionError if CIO user message exceeds fail gate (for local tests)."""
    diag = diagnose_cio_packet(
        prior_structured,
        session,
        cio_symbols=cio_symbols,
        analysis_symbols=analysis_symbols,
    )
    if not diag["within_fail_gate"]:
        raise AssertionError(
            f"CIO user message {diag['cio_user_message_chars']} chars exceeds "
            f"fail gate {CIO_MESSAGE_FAIL_CHARS}"
        )
    return diag
