from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CSV_COLUMNS = (
    "date",
    "session",
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
) -> Path:
    """Read a candidate JSONL file and write a review CSV. Returns the CSV path."""
    jsonl_path = jsonl_path.resolve()
    if not jsonl_path.is_file():
        raise FileNotFoundError(f"Candidate JSONL not found: {jsonl_path}")

    records = _load_jsonl(jsonl_path)
    if not records:
        raise ValueError(f"No candidate records in {jsonl_path}")

    csv_path = (output_path or review_csv_path_for_jsonl(jsonl_path, reviews_dir)).resolve()
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow(_csv_row(record))

    logger.info(
        "Candidate review export wrote %s rows to %s",
        len(records),
        csv_path,
    )
    return csv_path
