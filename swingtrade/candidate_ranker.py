from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_NO_CLEAN_SETUP = "No Clean Setup"
_NO_TRADE = "No Trade"

_SETUP_QUALITY_BONUS = {
    "A": 1.0,
    "B": 0.6,
    "C": 0.2,
    "NO TRADE": 0.0,
}

_STRATEGY_BONUS = {
    "MOMENTUM": 1.0,
    "BREAKOUT": 1.0,
    "PULLBACK": 0.55,
    _NO_CLEAN_SETUP.upper(): 0.0,
}

_RS_ADJUST = {
    "OUTPERFORMING": 0.05,
    "IN LINE": 0.0,
    "UNDERPERFORMING": -0.10,
}


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


def _norm_score_0_10(value: Any) -> float:
    v = _as_float(value)
    if v is None:
        return 0.0
    return max(0.0, min(10.0, v)) / 10.0


def _row_str(row: dict[str, Any], key: str) -> str:
    v = row.get(key)
    if v is None:
        return ""
    return str(v).strip()


def _ta_row(
    ta_structured: dict[str, Any], symbol: str
) -> dict[str, Any] | None:
    tickers = ta_structured.get("tickers")
    if not isinstance(tickers, dict):
        return None
    row = tickers.get(symbol) or tickers.get(symbol.upper())
    return row if isinstance(row, dict) else None


def _sentiment_score(
    sentiment_structured: dict[str, Any], symbol: str
) -> float:
    per = sentiment_structured.get("per_ticker")
    if not isinstance(per, dict):
        return 0.0
    block = per.get(symbol) or per.get(symbol.upper())
    if not isinstance(block, dict):
        return 0.0
    return _norm_score_0_10(block.get("score_0_10"))


def _rank_score(
    row: dict[str, Any] | None,
    sentiment_structured: dict[str, Any],
    symbol: str,
) -> float:
    if not row:
        return 0.0

    strategy = _row_str(row, "strategy_match").upper()
    setup_quality = _row_str(row, "setup_quality").upper()
    if strategy == _NO_CLEAN_SETUP.upper() or setup_quality == _NO_TRADE:
        return 0.0

    ta_part = _norm_score_0_10(row.get("ta_score")) * 0.45
    sent_part = _sentiment_score(sentiment_structured, symbol) * 0.20

    rr = _as_float(row.get("risk_reward"))
    rr_part = (min(rr / 3.0, 1.0) if rr is not None and rr > 0 else 0.0) * 0.15

    quality_part = _SETUP_QUALITY_BONUS.get(setup_quality, 0.1) * 0.10
    strategy_part = _STRATEGY_BONUS.get(strategy, 0.3) * 0.10

    rs = _row_str(row, "relative_strength_vs_qqq").upper()
    rs_adj = _RS_ADJUST.get(rs, 0.0)

    mom = _row_str(row, "momentum_status").upper()
    extension_penalty = 0.05 if mom == "EXTENDED" else 0.0

    cap_penalty = 0.0
    reasons = row.get("score_cap_reasons")
    if isinstance(reasons, list) and reasons:
        cap_penalty = 0.03

    return max(
        0.0,
        ta_part
        + sent_part
        + rr_part
        + quality_part
        + strategy_part
        + rs_adj
        - extension_penalty
        - cap_penalty,
    )


def rank_analysis_pool(
    prior_structured: dict[str, dict[str, Any]],
    analysis_symbols: list[str],
) -> list[tuple[str, float]]:
    """Rank all *analysis_symbols* by rank_score (highest first)."""
    ta = prior_structured.get("technical_analysis")
    if not isinstance(ta, dict):
        ta = {}
    se = prior_structured.get("sentiment")
    if not isinstance(se, dict):
        se = {}

    ranked: list[tuple[str, float]] = []
    for sym in analysis_symbols:
        key = sym.strip().upper()
        if not key:
            continue
        score = _rank_score(_ta_row(ta, key), se, key)
        ranked.append((key, score))

    ranked.sort(key=lambda x: (-x[1], x[0]))
    return ranked


def rank_for_cio(
    prior_structured: dict[str, dict[str, Any]],
    analysis_symbols: list[str],
    *,
    max_cio: int = 12,
) -> list[str]:
    """Return top *max_cio* symbols for CIO review (highest rank_score first)."""
    if max_cio <= 0 or not analysis_symbols:
        return []

    ranked = rank_analysis_pool(prior_structured, analysis_symbols)

    positive = [s for s, sc in ranked if sc > 0]
    pool = positive if positive else [s for s, _ in ranked]
    out = pool[:max_cio]

    logger.info(
        "CIO ranker: %s analysed -> %s selected for CIO (max_cio=%s)",
        len(analysis_symbols),
        len(out),
        max_cio,
    )
    if out:
        top = ", ".join(f"{s}({sc:.2f})" for s, sc in ranked[: min(5, len(ranked))])
        logger.debug("CIO ranker top scores: %s", top)

    return out
