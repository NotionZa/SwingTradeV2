from __future__ import annotations

import logging
from typing import Any

from anthropic import Anthropic

from swingtrade.agents.sentiment import run_sentiment
from swingtrade.agents.technical import discord_markdown_from_structured, run_technical
from swingtrade.models.agents import AgentResult, RunContext
from swingtrade.settings import Settings

logger = logging.getLogger(__name__)

DEFAULT_ANALYSIS_BATCH_SIZE = 15


def chunk_symbols(symbols: list[str], batch_size: int) -> list[list[str]]:
    """Split *symbols* into stable-order batches of at most *batch_size*."""
    if batch_size <= 0:
        batch_size = DEFAULT_ANALYSIS_BATCH_SIZE
    if not symbols:
        return []
    return [symbols[i : i + batch_size] for i in range(0, len(symbols), batch_size)]


def _norm_symbol(sym: str) -> str:
    return sym.strip().upper()


def _merge_str_dicts(parts: list[dict[str, Any]], key: str) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for part in parts:
        block = part.get(key)
        if not isinstance(block, dict):
            continue
        for sym, val in block.items():
            k = _norm_symbol(str(sym))
            if k:
                merged[k] = val
    return merged


def merge_technical_structured(
    parts: list[dict[str, Any]],
    *,
    batch_count: int,
    symbol_count: int,
    batch_size: int = DEFAULT_ANALYSIS_BATCH_SIZE,
) -> dict[str, Any]:
    """Merge batched Technical structured payloads into one dict."""
    tickers = _merge_str_dicts(parts, "tickers")
    scores = _merge_str_dicts(parts, "scores")
    inputs = _merge_str_dicts(parts, "inputs")

    for sym, row in tickers.items():
        if not isinstance(row, dict):
            continue
        capped = row.get("ta_score")
        if capped is not None:
            scores[sym] = capped

    notes_parts: list[str] = []
    for i, part in enumerate(parts, start=1):
        notes = part.get("notes")
        if isinstance(notes, str) and notes.strip():
            if batch_count > 1:
                notes_parts.append(f"**Batch {i}/{batch_count}**\n{notes.strip()}")
            else:
                notes_parts.append(notes.strip())

    merged_notes = "\n\n---\n\n".join(notes_parts)
    if batch_count > 1:
        header = (
            f"_Merged Technical output from {batch_count} batches "
            f"({symbol_count} symbols)._"
        )
        merged_notes = f"{header}\n\n{merged_notes}" if merged_notes else header

    out: dict[str, Any] = {
        "tickers": tickers,
        "scores": scores,
        "inputs": inputs,
        "notes": merged_notes,
        "_batching": {
            "batch_size": batch_size,
            "batches": batch_count,
            "symbols": symbol_count,
        },
    }
    return out


def _merge_sentiment_macro(macros: list[dict[str, Any]]) -> dict[str, Any]:
    scores: list[float] = []
    catalysts: list[str] = []
    for macro in macros:
        if not isinstance(macro, dict):
            continue
        raw_score = macro.get("score_0_10")
        if isinstance(raw_score, (int, float)):
            scores.append(float(raw_score))
        catalyst = macro.get("catalyst")
        if isinstance(catalyst, str) and catalyst.strip():
            catalysts.append(catalyst.strip())

    score_out = int(round(sum(scores) / len(scores))) if scores else 5
    score_out = max(0, min(10, score_out))
    if not catalysts:
        catalyst_text = "Mixed macro read across analysis batches."
    elif len(catalysts) == 1:
        catalyst_text = catalysts[0]
    else:
        catalyst_text = "; ".join(catalysts[:4])
        if len(catalysts) > 4:
            catalyst_text += f" (+{len(catalysts) - 4} more)"
    return {"score_0_10": score_out, "catalyst": catalyst_text}


def merge_sentiment_structured(
    parts: list[dict[str, Any]],
    *,
    batch_count: int,
    symbol_count: int,
) -> dict[str, Any]:
    """Merge batched Sentiment structured payloads into one dict."""
    per_ticker = _merge_str_dicts(parts, "per_ticker")
    raw_parts: list[dict[str, Any]] = []
    macros: list[dict[str, Any]] = []
    for part in parts:
        macro = part.get("macro")
        if isinstance(macro, dict):
            macros.append(macro)
        raw = part.get("raw_bundle")
        if isinstance(raw, dict):
            raw_parts.append(raw)

    raw_per = _merge_str_dicts(raw_parts, "per_ticker")
    raw_bundle: dict[str, Any] = {"per_ticker": raw_per}
    for raw in raw_parts:
        for key, val in raw.items():
            if key != "per_ticker" and key not in raw_bundle:
                raw_bundle[key] = val

    return {
        "macro": _merge_sentiment_macro(macros),
        "per_ticker": per_ticker,
        "raw_bundle": raw_bundle,
        "_batching": {
            "batches": batch_count,
            "symbols": symbol_count,
        },
    }


def _market_news_section(structured: dict[str, Any]) -> list[str]:
    """## Market news section from merged raw_bundle (compatible with format_news_digest)."""
    raw = structured.get("raw_bundle") or {}
    per = (raw.get("per_ticker") or {}) if isinstance(raw, dict) else {}
    lines = ["## Market news", ""]
    if not isinstance(per, dict) or not per:
        lines.append("_No headline bundle._")
        return lines

    for sym in sorted(per.keys()):
        block = per[sym]
        if not isinstance(block, dict):
            continue
        news = block.get("news") or []
        lines.append(f"**{sym}**")
        if not news:
            lines.append("- _(no headlines)_")
            lines.append("")
            continue
        for item in news[:4]:
            if not isinstance(item, dict):
                continue
            title = item.get("headline") or item.get("title") or ""
            src = item.get("source") or ""
            lines.append(f"- {title} _({src})_")
        lines.append("")
    return lines


def sentiment_discord_markdown_from_structured(
    structured: dict[str, Any],
    session: str,
) -> str:
    """Deterministic Sentiment/Macro Discord post from merged structured output."""
    session_l = str(session).replace("_", " ").title()
    macro = structured.get("macro")
    if not isinstance(macro, dict):
        macro = {}

    score = macro.get("score_0_10", "n/a")
    catalyst = macro.get("catalyst") or "_No macro catalyst._"
    batching = structured.get("_batching")
    lines = [
        f"**SwingTrader — Sentiment | {session_l}**",
        "",
        "## Macro/Tech",
        "",
        f"- **Macro score (0–10):** {score}",
        f"- **Catalyst:** {catalyst}",
    ]
    if isinstance(batching, dict) and batching.get("batches", 1) > 1:
        lines.append(
            f"\n_Batched analysis: {batching.get('batches')} Sentiment batches, "
            f"{batching.get('symbols')} symbols merged._"
        )
    lines.append("")

    per = structured.get("per_ticker")
    if isinstance(per, dict) and per:
        lines.extend(["### Per-ticker tone", ""])
        for sym in sorted(per.keys()):
            block = per[sym]
            if not isinstance(block, dict):
                continue
            s = block.get("score_0_10", "n/a")
            c = block.get("catalyst") or "—"
            lines.append(f"- **{sym}** ({s}/10): {c}")
        lines.append("")

    lines.extend(_market_news_section(structured))
    md = "\n".join(lines).strip()
    return md or "_No sentiment output_"


def run_technical_batched(
    settings: Settings,
    ctx: RunContext,
    client: Anthropic,
    symbols: list[str],
    *,
    batch_size: int = DEFAULT_ANALYSIS_BATCH_SIZE,
) -> AgentResult:
    if batch_size <= 0:
        batch_size = DEFAULT_ANALYSIS_BATCH_SIZE
    if len(symbols) <= batch_size:
        return run_technical(settings, ctx, client, symbols)

    batches = chunk_symbols(symbols, batch_size)
    logger.info(
        "Technical agent: %s symbols in %s batches (batch_size=%s)",
        len(symbols),
        len(batches),
        batch_size,
    )

    batch_results: list[AgentResult] = []
    for idx, batch in enumerate(batches, start=1):
        logger.info(
            "Technical agent: batch %s/%s (%s symbols)",
            idx,
            len(batches),
            len(batch),
        )
        batch_results.append(run_technical(settings, ctx, client, batch))

    merged = merge_technical_structured(
        [r.structured for r in batch_results],
        batch_count=len(batches),
        symbol_count=len(symbols),
        batch_size=batch_size,
    )
    per = merged.get("inputs")
    md = (
        discord_markdown_from_structured(
            merged,
            ctx.session,
            per if isinstance(per, dict) else None,
        )
        or "_No TA output_"
    ).rstrip()

    model_used = batch_results[0].model_used
    if len(batches) > 1:
        model_used = f"{model_used} ({len(batches)} batches)"

    logger.info(
        "Technical agent: merged %s batches -> %s tickers, %s scores",
        len(batches),
        len(merged.get("tickers") or {}),
        len(merged.get("scores") or {}),
    )

    return AgentResult(
        agent_id="technical_analysis",
        discord_markdown=md,
        structured=merged,
        model_used=model_used,
    )


def run_sentiment_batched(
    settings: Settings,
    ctx: RunContext,
    client: Anthropic,
    symbols: list[str],
    *,
    batch_size: int = DEFAULT_ANALYSIS_BATCH_SIZE,
) -> AgentResult:
    if batch_size <= 0:
        batch_size = DEFAULT_ANALYSIS_BATCH_SIZE
    if len(symbols) <= batch_size:
        return run_sentiment(settings, ctx, client, symbols)

    batches = chunk_symbols(symbols, batch_size)
    logger.info(
        "Sentiment agent: %s symbols in %s batches (batch_size=%s)",
        len(symbols),
        len(batches),
        batch_size,
    )

    batch_results: list[AgentResult] = []
    for idx, batch in enumerate(batches, start=1):
        logger.info(
            "Sentiment agent: batch %s/%s (%s symbols)",
            idx,
            len(batches),
            len(batch),
        )
        batch_results.append(run_sentiment(settings, ctx, client, batch))

    merged = merge_sentiment_structured(
        [r.structured for r in batch_results],
        batch_count=len(batches),
        symbol_count=len(symbols),
    )
    md = sentiment_discord_markdown_from_structured(merged, ctx.session)

    model_used = batch_results[0].model_used
    if len(batches) > 1:
        model_used = f"{model_used} ({len(batches)} batches)"

    logger.info(
        "Sentiment agent: merged %s batches -> %s per_ticker entries",
        len(batches),
        len(merged.get("per_ticker") or {}),
    )

    return AgentResult(
        agent_id="sentiment",
        discord_markdown=md,
        structured=merged,
        model_used=model_used,
    )
