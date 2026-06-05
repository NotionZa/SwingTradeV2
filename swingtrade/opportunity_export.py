from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Any

from swingtrade.candidate_review import (
    _load_jsonl,
    enrich_records_for_review_export,
    latest_run_timestamp_utc,
    select_records_for_review_export,
)
from swingtrade.trade_math import (
    OPPORTUNITY_BUY_NOW,
    OPPORTUNITY_NEAR_BUY,
    OPPORTUNITY_NO_ZONE,
    OPPORTUNITY_PULLBACK_REQUIRED,
    OPPORTUNITY_TARGET_EXTENSION,
)

logger = logging.getLogger(__name__)

OPPORTUNITY_CSV_COLUMNS = (
    "date",
    "session",
    "ticker",
    "review_level",
    "analysis_rank",
    "rank_score",
    "decision",
    "direction",
    "strategy",
    "conviction",
    "cio_score",
    "ta_score",
    "sentiment_score",
    "risk_reward",
    "model_risk_reward",
    "math_valid",
    "math_warning",
    "opportunity_status",
    "required_rr",
    "valid_entry_max",
    "current_rr",
    "rr_gap",
    "entry_improvement_needed",
    "zone_low",
    "zone_mid",
    "zone_high",
    "rr_at_zone_low",
    "rr_at_zone_mid",
    "rr_at_zone_high",
    "planned_entry_price",
    "planned_entry_rr",
    "conditional_buy_limit",
    "qty_for_1000_notional",
    "risk_per_share_at_planned_entry",
    "reward_per_share_at_planned_entry",
    "entry_zone",
    "stop_loss",
    "target",
    "market_regime",
    "tech_bias",
    "overall_risk_level",
    "reason",
    "action_required",
    "revisit_condition",
    "opportunity_note",
)

_DEFAULT_EXPORT_STATUSES = frozenset(
    {
        OPPORTUNITY_BUY_NOW,
        OPPORTUNITY_NEAR_BUY,
        OPPORTUNITY_PULLBACK_REQUIRED,
        OPPORTUNITY_TARGET_EXTENSION,
    }
)

_STATUS_SORT_ORDER = {
    OPPORTUNITY_BUY_NOW: 0,
    OPPORTUNITY_NEAR_BUY: 1,
    OPPORTUNITY_PULLBACK_REQUIRED: 2,
    OPPORTUNITY_TARGET_EXTENSION: 3,
    OPPORTUNITY_NO_ZONE: 4,
}


def default_opportunities_dir() -> Path:
    return Path.cwd().resolve() / "data" / "opportunities"


def opportunity_csv_path_for_jsonl(
    jsonl_path: Path,
    opportunities_dir: Path | None = None,
) -> Path:
    """Map ``data/candidates/2026-06-03_post_market.jsonl`` → ``data/opportunities/..._opportunities.csv``."""
    stem = jsonl_path.stem
    out_dir = opportunities_dir or default_opportunities_dir()
    return out_dir / f"{stem}_opportunities.csv"


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _normalize_opportunity_status(record: dict[str, Any]) -> str:
    status = record.get("opportunity_status")
    if isinstance(status, str) and status.strip():
        return status.strip().upper()
    return OPPORTUNITY_NO_ZONE


def filter_opportunity_records(
    records: list[dict[str, Any]],
    *,
    include_no_zone: bool = False,
) -> list[dict[str, Any]]:
    """Keep rows with actionable opportunity_status (excludes NO_ACTIONABLE_ZONE by default)."""
    allowed = set(_DEFAULT_EXPORT_STATUSES)
    if include_no_zone:
        allowed.add(OPPORTUNITY_NO_ZONE)
    out: list[dict[str, Any]] = []
    for record in records:
        status = _normalize_opportunity_status(record)
        if status in allowed:
            out.append(record)
    return out


def sort_opportunity_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort by status priority, then rr_gap, entry_improvement_needed, rank_score."""

    def _key(record: dict[str, Any]) -> tuple[float, float, float, float, str]:
        status = _normalize_opportunity_status(record)
        status_rank = float(_STATUS_SORT_ORDER.get(status, 99))
        rr_gap = _as_float(record.get("rr_gap"))
        entry_imp = _as_float(record.get("entry_improvement_needed"))
        rank = _as_float(record.get("rank_score"))
        return (
            status_rank,
            rr_gap if rr_gap is not None else float("inf"),
            entry_imp if entry_imp is not None else float("inf"),
            -(rank if rank is not None else float("-inf")),
            str(record.get("ticker") or ""),
        )

    return sorted(records, key=_key)


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _opportunity_csv_row(record: dict[str, Any]) -> dict[str, str]:
    return {col: _cell(record.get(col)) for col in OPPORTUNITY_CSV_COLUMNS}


def export_opportunity_csv(
    jsonl_path: Path,
    *,
    output_path: Path | None = None,
    opportunities_dir: Path | None = None,
    all_runs: bool = False,
    include_no_zone: bool = False,
) -> Path:
    """Export tactical opportunity watchlist CSV from candidate JSONL."""
    jsonl_path = jsonl_path.resolve()
    if not jsonl_path.is_file():
        raise FileNotFoundError(f"Candidate JSONL not found: {jsonl_path}")

    loaded = _load_jsonl(jsonl_path)
    if not loaded:
        raise ValueError(f"No candidate records in {jsonl_path}")

    records = select_records_for_review_export(loaded, all_runs=all_runs)
    if not records:
        raise ValueError(f"No candidate records to export from {jsonl_path}")

    records = enrich_records_for_review_export(records)
    records = filter_opportunity_records(records, include_no_zone=include_no_zone)
    records = sort_opportunity_records(records)

    csv_path = (
        output_path or opportunity_csv_path_for_jsonl(jsonl_path, opportunities_dir)
    ).resolve()
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=OPPORTUNITY_CSV_COLUMNS,
            extrasaction="ignore",
        )
        writer.writeheader()
        for record in records:
            writer.writerow(_opportunity_csv_row(record))

    latest = latest_run_timestamp_utc(loaded)
    logger.info(
        "Opportunity export wrote %s rows to %s (loaded=%s, latest_run=%s, "
        "all_runs=%s, include_no_zone=%s)",
        len(records),
        csv_path,
        len(loaded),
        latest or "n/a",
        all_runs,
        include_no_zone,
    )
    return csv_path
