from __future__ import annotations

import csv
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from swingtrade.outcome_tracker import (
    EXIT_DATA_ERROR,
    EXIT_INVALID,
    EXIT_LOSS,
    EXIT_NO_ENTRY,
    EXIT_OPEN,
    EXIT_WIN,
    default_backtests_dir,
)
from swingtrade.run_identity import enrich_run_identity
from swingtrade.trade_math import _as_float

logger = logging.getLogger(__name__)

SUMMARY_GROUP_KEYS = (
    "session",
    "decision",
    "review_level",
    "opportunity_status",
    "strategy",
)

BY_RUN_GROUP_KEYS = (
    "run_id",
    "date",
    "session",
    "decision",
    "review_level",
    "opportunity_status",
    "strategy",
)

METRIC_COLUMNS = (
    "rows",
    "valid_rows",
    "entries_triggered",
    "wins",
    "losses",
    "no_entry",
    "open",
    "invalid",
    "win_rate_closed",
    "avg_r_triggered",
    "avg_r_closed",
    "total_r_closed",
    "expectancy_closed",
)

SUMMARY_CSV_COLUMNS = SUMMARY_GROUP_KEYS + METRIC_COLUMNS
BY_RUN_CSV_COLUMNS = BY_RUN_GROUP_KEYS + METRIC_COLUMNS

_SUMMARY_OUTPUT_NAMES = frozenset({"summary.csv", "summary_by_run.csv"})


def _normalize_exit_type(row: dict[str, Any]) -> str:
    return str(row.get("exit_type") or "").strip().upper()


def _entry_triggered(row: dict[str, Any]) -> bool:
    val = row.get("entry_triggered")
    return val in (True, "true", "True", "1", 1)


def _group_value(row: dict[str, Any], key: str) -> str:
    val = row.get(key)
    if val is None:
        return ""
    text = str(val).strip()
    return text


def _group_key(row: dict[str, Any], keys: Sequence[str]) -> tuple[str, ...]:
    return tuple(_group_value(row, k) for k in keys)


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def _sum(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values), 4)


@dataclass
class _Accumulator:
    rows: int = 0
    valid_rows: int = 0
    entries_triggered: int = 0
    wins: int = 0
    losses: int = 0
    no_entry: int = 0
    open_count: int = 0
    invalid: int = 0
    r_triggered: list[float] = field(default_factory=list)
    r_closed: list[float] = field(default_factory=list)

    def add(self, row: dict[str, Any]) -> None:
        self.rows += 1
        exit_type = _normalize_exit_type(row)

        if exit_type in (EXIT_INVALID, EXIT_DATA_ERROR):
            self.invalid += 1
            return

        self.valid_rows += 1

        if _entry_triggered(row):
            self.entries_triggered += 1

        if exit_type == EXIT_WIN:
            self.wins += 1
        elif exit_type == EXIT_LOSS:
            self.losses += 1
        elif exit_type == EXIT_NO_ENTRY:
            self.no_entry += 1
        elif exit_type == EXIT_OPEN:
            self.open_count += 1

        r_val = _as_float(row.get("r_multiple"))
        if r_val is not None and exit_type in (EXIT_WIN, EXIT_LOSS, EXIT_OPEN):
            self.r_triggered.append(r_val)
        if r_val is not None and exit_type in (EXIT_WIN, EXIT_LOSS):
            self.r_closed.append(r_val)

    def to_metrics(self) -> dict[str, Any]:
        closed = self.wins + self.losses
        win_rate = round(self.wins / closed * 100.0, 2) if closed else None
        avg_r_triggered = _avg(self.r_triggered)
        avg_r_closed = _avg(self.r_closed)
        total_r_closed = _sum(self.r_closed)
        expectancy = avg_r_closed
        return {
            "rows": self.rows,
            "valid_rows": self.valid_rows,
            "entries_triggered": self.entries_triggered,
            "wins": self.wins,
            "losses": self.losses,
            "no_entry": self.no_entry,
            "open": self.open_count,
            "invalid": self.invalid,
            "win_rate_closed": win_rate,
            "avg_r_triggered": avg_r_triggered,
            "avg_r_closed": avg_r_closed,
            "total_r_closed": total_r_closed,
            "expectancy_closed": expectancy,
        }


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def load_outcome_csv(path: Path) -> list[dict[str, Any]]:
    """Load one outcome CSV; enrich run identity when possible."""
    path = path.resolve()
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for line_no, raw in enumerate(reader, start=2):
            if not raw:
                continue
            row = enrich_run_identity(dict(raw))
            row["_source_file"] = path.name
            rows.append(row)
    logger.info("Loaded %s rows from %s", len(rows), path)
    return rows


def discover_outcome_csvs(input_path: Path) -> list[Path]:
    """Find *_outcomes.csv files under a directory (or a single file path)."""
    input_path = input_path.resolve()
    if input_path.is_file():
        return [input_path]
    if not input_path.is_dir():
        raise FileNotFoundError(f"Backtest input not found: {input_path}")

    files = sorted(
        p
        for p in input_path.glob("*_outcomes.csv")
        if p.is_file() and p.name not in _SUMMARY_OUTPUT_NAMES
    )
    if not files:
        raise ValueError(f"No *_outcomes.csv files in {input_path}")
    return files


def load_outcome_rows(input_path: Path) -> list[dict[str, Any]]:
    """Load one or many outcome CSV files."""
    files = discover_outcome_csvs(input_path)
    rows: list[dict[str, Any]] = []
    for path in files:
        try:
            rows.extend(load_outcome_csv(path))
        except OSError as exc:
            logger.warning("Skipping unreadable outcome file %s: %s", path, exc)
    if not rows:
        raise ValueError(f"No outcome rows loaded from {input_path}")
    return rows


def aggregate_outcome_rows(
    rows: Iterable[dict[str, Any]],
    *,
    group_keys: Sequence[str],
) -> list[dict[str, Any]]:
    """Aggregate outcome rows by the given dimension keys."""
    buckets: dict[tuple[str, ...], _Accumulator] = defaultdict(_Accumulator)
    for row in rows:
        key = _group_key(row, group_keys)
        buckets[key].add(row)

    out: list[dict[str, Any]] = []
    for key in sorted(buckets.keys()):
        record = {group_keys[i]: key[i] for i in range(len(group_keys))}
        record.update(buckets[key].to_metrics())
        out.append(record)
    return out


def build_summary_tables(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (overall summary, by-run summary) row lists."""
    overall = aggregate_outcome_rows(rows, group_keys=SUMMARY_GROUP_KEYS)
    by_run = aggregate_outcome_rows(rows, group_keys=BY_RUN_GROUP_KEYS)
    return overall, by_run


def export_summary_csv(
    rows: list[dict[str, Any]],
    *,
    output_path: Path,
    columns: Sequence[str],
) -> Path:
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(columns), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({col: _cell(row.get(col)) for col in columns})
    return output_path


def run_backtest_summary(
    input_path: Path | None = None,
    *,
    output_path: Path | None = None,
    by_run_output_path: Path | None = None,
) -> tuple[Path, Path, list[dict[str, Any]], list[dict[str, Any]]]:
    """Load outcome CSVs, write summary.csv and summary_by_run.csv."""
    in_dir = (input_path or default_backtests_dir()).resolve()
    out_summary = (output_path or in_dir / "summary.csv").resolve()
    out_by_run = (by_run_output_path or in_dir / "summary_by_run.csv").resolve()

    rows = load_outcome_rows(in_dir)
    overall, by_run = build_summary_tables(rows)

    export_summary_csv(overall, output_path=out_summary, columns=SUMMARY_CSV_COLUMNS)
    export_summary_csv(by_run, output_path=out_by_run, columns=BY_RUN_CSV_COLUMNS)

    logger.info(
        "Backtest summary wrote %s overall groups to %s and %s by-run groups to %s "
        "(loaded_rows=%s, source=%s)",
        len(overall),
        out_summary,
        len(by_run),
        out_by_run,
        len(rows),
        in_dir,
    )
    return out_summary, out_by_run, overall, by_run, len(rows)


def format_summary_console(
    overall: list[dict[str, Any]],
    by_run: list[dict[str, Any]],
    *,
    loaded_rows: int,
) -> str:
    """Compact console summary for CLI."""
    lines = [
        "Backtest summary aggregator",
        f"  loaded outcome rows: {loaded_rows}",
        f"  overall groups: {len(overall)}",
        f"  by-run groups: {len(by_run)}",
    ]
    if overall:
        top = max(
            overall,
            key=lambda r: (r.get("wins", 0) + r.get("losses", 0), r.get("rows", 0)),
        )
        lines.append(
            "  top closed-trade group: "
            f"session={top.get('session')} decision={top.get('decision')} "
            f"opportunity_status={top.get('opportunity_status')} "
            f"wins={top.get('wins')} losses={top.get('losses')} "
            f"win_rate_closed={top.get('win_rate_closed')}"
        )
    return "\n".join(lines)
