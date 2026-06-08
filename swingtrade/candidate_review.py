from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Any

from swingtrade.export_archive import write_archive_snapshot
from swingtrade.run_identity import enrich_run_identity
from swingtrade.trade_math import enrich_candidate_trade_fields

logger = logging.getLogger(__name__)

CSV_COLUMNS = (
    "date",
    "session",
    "run_timestamp_utc",
    "run_id",
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
    "opportunity_note",
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
    "ta_direction",
    "ta_strategy",
    "ta_entry_zone",
    "ta_stop_loss",
    "ta_target",
    "ta_risk_reward",
    "ta_math_valid",
    "revisit_opportunity_status",
    "revisit_required_rr",
    "revisit_valid_entry_max",
    "revisit_current_rr",
    "revisit_rr_gap",
    "revisit_entry_improvement_needed",
    "revisit_opportunity_note",
    "revisit_planned_entry_price",
    "revisit_planned_entry_rr",
    "revisit_conditional_buy_limit",
    "entry_zone",
    "stop_loss",
    "target",
    "market_regime",
    "tech_bias",
    "overall_risk_level",
    "reason",
    "action_required",
    "revisit_condition",
)

def default_reviews_dir() -> Path:
    return Path.cwd().resolve() / "data" / "reviews"


def review_csv_path_for_jsonl(jsonl_path: Path, reviews_dir: Path | None = None) -> Path:
    """Map ``data/candidates/2026-05-21_pre_market.jsonl`` → ``data/reviews/2026-05-21_pre_market_review.csv``."""
    stem = jsonl_path.stem
    out_dir = reviews_dir or default_reviews_dir()
    return out_dir / f"{stem}_review.csv"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as e:
                logger.warning("Skipping invalid JSON on line %s of %s: %s", line_no, path, e)
                continue
            if isinstance(row, dict):
                records.append(row)
    return records


def _run_timestamp_utc(record: dict[str, Any]) -> str:
    ts = record.get("run_timestamp_utc")
    if isinstance(ts, str) and ts.strip():
        return ts.strip()
    return ""


def _normalize_ticker(record: dict[str, Any]) -> str:
    ticker = record.get("ticker")
    if isinstance(ticker, str) and ticker.strip():
        return ticker.strip().upper()
    return ""


def latest_run_timestamp_utc(records: list[dict[str, Any]]) -> str | None:
    """Return the newest run_timestamp_utc in *records* (ISO-8601 string compare)."""
    stamps = [_run_timestamp_utc(r) for r in records]
    stamps = [s for s in stamps if s]
    if not stamps:
        return None
    return max(stamps)


def dedupe_records_by_ticker_last(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep the last JSONL row per ticker (stable first-seen ticker order)."""
    order: list[str] = []
    by_ticker: dict[str, dict[str, Any]] = {}
    for record in records:
        sym = _normalize_ticker(record)
        if not sym:
            continue
        if sym not in by_ticker:
            order.append(sym)
        by_ticker[sym] = record
    return [by_ticker[sym] for sym in order]


def select_records_for_review_export(
    records: list[dict[str, Any]],
    *,
    all_runs: bool = False,
) -> list[dict[str, Any]]:
    """Default: latest run_timestamp_utc only, last row per ticker. --all-runs: full JSONL."""
    if all_runs:
        return list(records)

    latest = latest_run_timestamp_utc(records)
    if latest is None:
        return dedupe_records_by_ticker_last(records)

    latest_run = [r for r in records if _run_timestamp_utc(r) == latest]
    return dedupe_records_by_ticker_last(latest_run)


def enrich_records_for_review_export(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Backfill trade-math, run identity, and opportunity-zone fields before CSV export."""
    out: list[dict[str, Any]] = []
    for record in records:
        row = enrich_run_identity(dict(record))
        row = enrich_candidate_trade_fields(row)
        out.append(row)
    return out


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _csv_row(record: dict[str, Any]) -> dict[str, str]:
    return {col: _cell(record.get(col)) for col in CSV_COLUMNS}


def export_candidate_review_csv(
    jsonl_path: Path,
    *,
    output_path: Path | None = None,
    reviews_dir: Path | None = None,
    all_runs: bool = False,
) -> Path:
    """Read a candidate JSONL file and write a review CSV. Returns the CSV path."""
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

    csv_path = (output_path or review_csv_path_for_jsonl(jsonl_path, reviews_dir)).resolve()
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow(_csv_row(record))

    write_archive_snapshot(csv_path, records)

    latest = latest_run_timestamp_utc(loaded)
    logger.info(
        "Candidate review export wrote %s rows to %s (loaded=%s, latest_run=%s, all_runs=%s)",
        len(records),
        csv_path,
        len(loaded),
        latest or "n/a",
        all_runs,
    )
    return csv_path
