from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from swingtrade.candidate_ranker import rank_analysis_pool
from swingtrade.models.agents import PipelineState, SessionName

logger = logging.getLogger(__name__)

_RANK_EXCLUDED_REASON = (
    "Not sent to CIO; ranked below CIO review cutoff."
)
_CIO_POOL_NO_DECISION_REASON = (
    "Sent to CIO pool but no CIO decision row returned for this ticker."
)

_DECISION_FIELDS = (
    "ticker",
    "decision",
    "direction",
    "strategy",
    "conviction",
    "cio_score",
    "ta_score",
    "sentiment_score",
    "risk_reward",
    "entry_zone",
    "stop_loss",
    "target",
    "targets",
    "position_size_guidance",
    "reason",
    "technical_thesis",
    "sentiment_catalyst",
    "macro_context",
    "invalidation_conditions",
    "action_required",
    "revisit_condition",
)

_SUMMARY_FIELDS = ("market_regime", "tech_bias", "overall_risk_level")

_SCREENED_DECISION = "SCREENED"


def default_candidates_dir() -> Path:
    return Path.cwd().resolve() / "data" / "candidates"


def _jsonl_path(session: SessionName, run_date: str, output_dir: Path) -> Path:
    return output_dir / f"{run_date}_{session}.jsonl"


def _coerce_decisions(structured: dict[str, Any]) -> list[dict[str, Any]]:
    raw = structured.get("decisions")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        ticker = item.get("ticker")
        if isinstance(ticker, str) and ticker.strip():
            out.append(item)
    return out


def _cio_decisions_by_ticker(
    structured: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for d in _coerce_decisions(structured):
        t = d.get("ticker")
        if isinstance(t, str) and t.strip():
            out[t.strip().upper()] = d
    return out


def _ta_structured(state: PipelineState) -> dict[str, Any]:
    ta = state.prior_structured.get("technical_analysis")
    return ta if isinstance(ta, dict) else {}


def _sentiment_structured(state: PipelineState) -> dict[str, Any]:
    se = state.prior_structured.get("sentiment")
    return se if isinstance(se, dict) else {}


def _ta_row(ta: dict[str, Any], symbol: str) -> dict[str, Any] | None:
    tickers = ta.get("tickers")
    if not isinstance(tickers, dict):
        return None
    row = tickers.get(symbol) or tickers.get(symbol.upper())
    return row if isinstance(row, dict) else None


def _sentiment_block(se: dict[str, Any], symbol: str) -> dict[str, Any] | None:
    per = se.get("per_ticker")
    if not isinstance(per, dict):
        return None
    block = per.get(symbol) or per.get(symbol.upper())
    return block if isinstance(block, dict) else None


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _base_record(
    *,
    session: SessionName,
    run_timestamp_utc: str,
    date: str,
    ticker: str,
    review_level: str,
    source: str,
    rank_score: float | None = None,
    analysis_rank: int | None = None,
    summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "run_timestamp_utc": run_timestamp_utc,
        "date": date,
        "session": session,
        "ticker": ticker,
        "review_level": review_level,
        "source": source,
    }
    if rank_score is not None:
        record["rank_score"] = round(rank_score, 4)
    if analysis_rank is not None:
        record["analysis_rank"] = analysis_rank
    if summary:
        for key in _SUMMARY_FIELDS:
            if key in summary and summary[key] is not None:
                record[key] = summary[key]
    return record


def _apply_ta_sentiment_fields(
    record: dict[str, Any],
    row: dict[str, Any] | None,
    sent_block: dict[str, Any] | None,
) -> None:
    if row:
        strategy = row.get("strategy_match")
        if strategy is not None:
            record["strategy"] = strategy
        ta_score = _as_float(row.get("ta_score"))
        if ta_score is not None:
            record["ta_score"] = ta_score
        rr = _as_float(row.get("risk_reward"))
        if rr is not None:
            record["risk_reward"] = rr
        entry = row.get("suggested_entry_zone")
        if entry is not None:
            record["entry_zone"] = entry
        stop = row.get("suggested_stop_loss")
        if stop is not None:
            record["stop_loss"] = stop
        target = row.get("suggested_target")
        if target is not None:
            record["target"] = target
        summary = row.get("summary")
        if isinstance(summary, str) and summary.strip():
            record["technical_thesis"] = summary.strip()
        cio_notes = row.get("cio_notes")
        if isinstance(cio_notes, str) and cio_notes.strip():
            record.setdefault("technical_thesis", cio_notes.strip())
        invalidation = row.get("invalidation_condition")
        if invalidation is not None:
            record["invalidation_conditions"] = [invalidation] if isinstance(
                invalidation, str
            ) else invalidation
        risks = row.get("technical_risks")
        if isinstance(risks, list) and risks and "technical_thesis" in record:
            pass
    if sent_block is not None:
        s = _as_float(sent_block.get("score_0_10"))
        if s is not None:
            record["sentiment_score"] = s
        catalyst = sent_block.get("catalyst")
        if isinstance(catalyst, str) and catalyst.strip():
            record["sentiment_catalyst"] = catalyst.strip()


def _build_cio_reviewed_record(
    decision: dict[str, Any],
    *,
    session: SessionName,
    run_timestamp_utc: str,
    date: str,
    summary: dict[str, Any],
    rank_score: float | None,
    analysis_rank: int | None,
) -> dict[str, Any]:
    record = _base_record(
        session=session,
        run_timestamp_utc=run_timestamp_utc,
        date=date,
        ticker=str(decision.get("ticker", "")).strip().upper(),
        review_level="cio_reviewed",
        source="cio",
        rank_score=rank_score,
        analysis_rank=analysis_rank,
        summary=summary,
    )
    for key in _DECISION_FIELDS:
        if key in decision and decision[key] is not None:
            record[key] = decision[key]
    record["decision_raw"] = dict(decision)
    return record


def _build_screened_record(
    symbol: str,
    *,
    session: SessionName,
    run_timestamp_utc: str,
    date: str,
    review_level: str,
    reason: str,
    summary: dict[str, Any],
    rank_score: float | None,
    analysis_rank: int | None,
    ta: dict[str, Any],
    se: dict[str, Any],
) -> dict[str, Any]:
    record = _base_record(
        session=session,
        run_timestamp_utc=run_timestamp_utc,
        date=date,
        ticker=symbol,
        review_level=review_level,
        source="pipeline",
        rank_score=rank_score,
        analysis_rank=analysis_rank,
        summary=summary,
    )
    record["decision"] = _SCREENED_DECISION
    record["reason"] = reason
    _apply_ta_sentiment_fields(
        record, _ta_row(ta, symbol), _sentiment_block(se, symbol)
    )
    return record


def _build_pipeline_records(
    state: PipelineState,
    session: SessionName,
    *,
    analysis_symbols: list[str],
    cio_symbols: list[str],
    run_timestamp_utc: str,
    date: str,
) -> list[dict[str, Any]]:
    if not analysis_symbols:
        return []

    ranked = rank_analysis_pool(state.prior_structured, analysis_symbols)
    rank_by_symbol = {sym: (idx + 1, score) for idx, (sym, score) in enumerate(ranked)}

    cio_set = {s.strip().upper() for s in cio_symbols if s and str(s).strip()}
    cio_structured = state.prior_structured.get("cio")
    cio_summary: dict[str, Any] = {}
    cio_by_ticker: dict[str, dict[str, Any]] = {}
    if isinstance(cio_structured, dict) and not cio_structured.get("stub"):
        summary = cio_structured.get("summary")
        if isinstance(summary, dict):
            cio_summary = summary
        cio_by_ticker = _cio_decisions_by_ticker(cio_structured)

    ta = _ta_structured(state)
    se = _sentiment_structured(state)

    records: list[dict[str, Any]] = []
    seen: set[str] = set()

    for sym, score in ranked:
        rank_idx, rank_sc = rank_by_symbol.get(sym, (None, score))
        if sym in cio_set and sym in cio_by_ticker:
            records.append(
                _build_cio_reviewed_record(
                    cio_by_ticker[sym],
                    session=session,
                    run_timestamp_utc=run_timestamp_utc,
                    date=date,
                    summary=cio_summary,
                    rank_score=rank_sc,
                    analysis_rank=rank_idx,
                )
            )
        elif sym in cio_set:
            records.append(
                _build_screened_record(
                    sym,
                    session=session,
                    run_timestamp_utc=run_timestamp_utc,
                    date=date,
                    review_level="technical_sentiment_only",
                    reason=_CIO_POOL_NO_DECISION_REASON,
                    summary=cio_summary,
                    rank_score=rank_sc,
                    analysis_rank=rank_idx,
                    ta=ta,
                    se=se,
                )
            )
        else:
            records.append(
                _build_screened_record(
                    sym,
                    session=session,
                    run_timestamp_utc=run_timestamp_utc,
                    date=date,
                    review_level="rank_excluded",
                    reason=_RANK_EXCLUDED_REASON,
                    summary=cio_summary,
                    rank_score=rank_sc,
                    analysis_rank=rank_idx,
                    ta=ta,
                    se=se,
                )
            )
        seen.add(sym)

    for sym in analysis_symbols:
        key = sym.strip().upper()
        if key and key not in seen:
            rank_idx, rank_sc = rank_by_symbol.get(key, (None, 0.0))
            records.append(
                _build_screened_record(
                    key,
                    session=session,
                    run_timestamp_utc=run_timestamp_utc,
                    date=date,
                    review_level="rank_excluded",
                    reason=_RANK_EXCLUDED_REASON,
                    summary=cio_summary,
                    rank_score=rank_sc,
                    analysis_rank=rank_idx,
                    ta=ta,
                    se=se,
                )
            )

    for sym, decision in cio_by_ticker.items():
        if sym not in seen:
            records.append(
                _build_cio_reviewed_record(
                    decision,
                    session=session,
                    run_timestamp_utc=run_timestamp_utc,
                    date=date,
                    summary=cio_summary,
                    rank_score=None,
                    analysis_rank=None,
                )
            )

    return records


def log_pipeline_candidates(
    state: PipelineState,
    session: SessionName,
    *,
    analysis_symbols: list[str] | None = None,
    cio_symbols: list[str] | None = None,
    output_dir: Path | None = None,
    dry_run: bool = False,
) -> int:
    """Append unified candidate rows (analysis pool + CIO) to JSONL. Returns rows written."""
    if dry_run:
        return 0

    analysis = list(analysis_symbols or state.analysis_tickers or state.tickers)
    cio_list = list(cio_symbols if cio_symbols is not None else state.tickers)

    if not analysis:
        return log_cio_candidates(state, session, output_dir=output_dir, dry_run=False)

    now = datetime.now(timezone.utc)
    run_timestamp_utc = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    date_str = now.strftime("%Y-%m-%d")

    records = _build_pipeline_records(
        state,
        session,
        analysis_symbols=analysis,
        cio_symbols=cio_list,
        run_timestamp_utc=run_timestamp_utc,
        date=date_str,
    )
    if not records:
        return 0

    out_dir = output_dir or default_candidates_dir()
    path = _jsonl_path(session, date_str, out_dir)

    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False, default=str))
                f.write("\n")
    except OSError as e:
        logger.warning("Candidate logger failed writing to %s: %s", path, e)
        return 0

    reviewed = sum(1 for r in records if r.get("review_level") == "cio_reviewed")
    excluded = sum(1 for r in records if r.get("review_level") == "rank_excluded")
    ts_only = sum(1 for r in records if r.get("review_level") == "technical_sentiment_only")

    try:
        path_display = path.relative_to(Path.cwd().resolve())
    except ValueError:
        path_display = path
    logger.info(
        "Candidate logger wrote %s records to %s "
        "(%s cio_reviewed, %s rank_excluded, %s technical_sentiment_only)",
        len(records),
        path_display,
        reviewed,
        excluded,
        ts_only,
    )
    return len(records)


def log_cio_candidates(
    state: PipelineState,
    session: SessionName,
    *,
    output_dir: Path | None = None,
    dry_run: bool = False,
) -> int:
    """Append CIO-only rows (legacy). Prefer log_pipeline_candidates when analysis pool is set."""
    if dry_run:
        return 0

    if state.analysis_tickers:
        return log_pipeline_candidates(
            state,
            session,
            analysis_symbols=state.analysis_tickers,
            cio_symbols=state.tickers,
            output_dir=output_dir,
            dry_run=False,
        )

    structured = state.prior_structured.get("cio")
    if not isinstance(structured, dict) or structured.get("stub"):
        return 0

    decisions = _coerce_decisions(structured)
    if not decisions:
        return 0

    summary = structured.get("summary")
    if not isinstance(summary, dict):
        summary = {}

    now = datetime.now(timezone.utc)
    run_timestamp_utc = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    date_str = now.strftime("%Y-%m-%d")

    out_dir = output_dir or default_candidates_dir()
    path = _jsonl_path(session, date_str, out_dir)

    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        records = [
            _build_cio_reviewed_record(
                d,
                session=session,
                run_timestamp_utc=run_timestamp_utc,
                date=date_str,
                summary=summary,
                rank_score=None,
                analysis_rank=None,
            )
            for d in decisions
        ]
        with path.open("a", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False, default=str))
                f.write("\n")
    except OSError as e:
        logger.warning("Candidate logger failed writing to %s: %s", path, e)
        return 0

    try:
        path_display = path.relative_to(Path.cwd().resolve())
    except ValueError:
        path_display = path
    logger.info(
        "Candidate logger wrote %s records to %s",
        len(records),
        path_display,
    )
    return len(records)
