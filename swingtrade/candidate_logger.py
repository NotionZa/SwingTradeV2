from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from swingtrade.models.agents import PipelineState, SessionName

logger = logging.getLogger(__name__)

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


def _build_record(
    decision: dict[str, Any],
    *,
    session: SessionName,
    run_timestamp_utc: str,
    date: str,
    summary: dict[str, Any],
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "run_timestamp_utc": run_timestamp_utc,
        "date": date,
        "session": session,
        "source": "cio",
    }
    for key in _DECISION_FIELDS:
        if key in decision and decision[key] is not None:
            record[key] = decision[key]
    for key in _SUMMARY_FIELDS:
        if key not in record and key in summary and summary[key] is not None:
            record[key] = summary[key]
    record["decision_raw"] = dict(decision)
    return record


def log_cio_candidates(
    state: PipelineState,
    session: SessionName,
    *,
    output_dir: Path | None = None,
    dry_run: bool = False,
) -> int:
    """Append CIO structured decisions to a local JSONL file. Returns records written."""
    if dry_run:
        return 0

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
            _build_record(
                d,
                session=session,
                run_timestamp_utc=run_timestamp_utc,
                date=date_str,
                summary=summary,
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
