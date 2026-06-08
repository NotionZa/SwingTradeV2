from __future__ import annotations

import csv
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
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
    "total_rows",
    "rows",
    "valid_rows",
    "invalid_rows",
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
    "entry_rate",
    "closed_rate",
    "no_entry_rate",
    "open_rate",
)

SUMMARY_CSV_COLUMNS = ("filter_label",) + SUMMARY_GROUP_KEYS + METRIC_COLUMNS
BY_RUN_CSV_COLUMNS = ("filter_label",) + BY_RUN_GROUP_KEYS + METRIC_COLUMNS

_SUMMARY_OUTPUT_NAMES = frozenset({"summary.csv", "summary_by_run.csv"})
_RUN_ID_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_")


@dataclass
class BacktestSummaryFilters:
    """Optional cohort filters applied after loading outcome CSV rows."""

    since: str | None = None
    until: str | None = None
    session: str | None = None
    decision: str | None = None
    review_level: str | None = None
    opportunity_status: str | None = None
    strategy: str | None = None
    only_valid_geometry: bool = False
    only_planned: bool = False
    only_conditional: bool = False
    only_closed: bool = False
    exclude_legacy: bool = False

    def label(self) -> str:
        """Compact label describing active filters (blank when none)."""
        parts: list[str] = []
        if self.since:
            parts.append(f"since={self.since}")
        if self.until:
            parts.append(f"until={self.until}")
        if self.session:
            parts.append(f"session={self.session}")
        if self.decision:
            parts.append(f"decision={self.decision}")
        if self.review_level:
            parts.append(f"review_level={self.review_level}")
        if self.opportunity_status:
            parts.append(f"opportunity_status={self.opportunity_status}")
        if self.strategy:
            parts.append(f"strategy={self.strategy}")
        if self.only_valid_geometry:
            parts.append("only_valid_geometry")
        if self.only_planned:
            parts.append("only_planned")
        if self.only_conditional:
            parts.append("only_conditional")
        if self.only_closed:
            parts.append("only_closed")
        if self.exclude_legacy:
            parts.append("exclude_legacy")
        return "|".join(parts)


def _normalize_exit_type(row: dict[str, Any]) -> str:
    return str(row.get("exit_type") or "").strip().upper()


def _entry_triggered(row: dict[str, Any]) -> bool:
    val = row.get("entry_triggered")
    return val in (True, "true", "True", "1", 1)


def _truthy(value: Any) -> bool:
    return value in (True, "true", "True", "1", 1)


def _group_value(row: dict[str, Any], key: str) -> str:
    val = row.get(key)
    if val is None:
        return ""
    return str(val).strip()


def _group_key(row: dict[str, Any], keys: Sequence[str]) -> tuple[str, ...]:
    return tuple(_group_value(row, k) for k in keys)


def _parse_iso_date(value: str | None) -> date | None:
    if not value or not str(value).strip():
        return None
    text = str(value).strip()[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def signal_date(row: dict[str, Any]) -> str:
    """Signal date from `date` column, else first segment of run_id."""
    explicit = _group_value(row, "date")
    if explicit:
        return explicit[:10]
    run_id = _group_value(row, "run_id")
    if run_id:
        match = _RUN_ID_DATE_RE.match(run_id)
        if match:
            return match.group(1)
    return ""


def is_legacy_outcome_row(row: dict[str, Any]) -> bool:
    """Conservative legacy detection for pre-N.22/N.23 tracking.

    A row is *modern* when it has run_id plus planned-entry or opportunity context.
    Rows missing run_id or both planned_entry_price and opportunity_status are legacy.
    """
    run_id = _group_value(row, "run_id")
    if not run_id:
        return True
    planned = _as_float(row.get("planned_entry_price"))
    if planned is not None:
        return False
    opp = _group_value(row, "opportunity_status")
    if opp and opp.upper() != "NO_ACTIONABLE_ZONE":
        return False
    return True


def has_valid_geometry(row: dict[str, Any]) -> bool:
    """Valid replay geometry: not INVALID/DATA_ERROR with entry/stop/target levels."""
    exit_type = _normalize_exit_type(row)
    if exit_type in (EXIT_INVALID, EXIT_DATA_ERROR):
        return False
    entry = _as_float(row.get("resolved_entry_price"))
    if entry is None:
        entry = _as_float(row.get("planned_entry_price"))
    if entry is None:
        entry = _as_float(row.get("valid_entry_max"))
    stop = _as_float(row.get("stop_loss"))
    target = _as_float(row.get("target"))
    return entry is not None and stop is not None and target is not None


def has_planned_entry(row: dict[str, Any]) -> bool:
    return _as_float(row.get("planned_entry_price")) is not None


def is_conditional_entry(row: dict[str, Any]) -> bool:
    return _truthy(row.get("conditional_buy_limit"))


def is_closed_outcome(row: dict[str, Any]) -> bool:
    return _normalize_exit_type(row) in (EXIT_WIN, EXIT_LOSS)


def apply_summary_filters(
    rows: Iterable[dict[str, Any]],
    filters: BacktestSummaryFilters | None,
) -> list[dict[str, Any]]:
    """Apply cohort filters; returns matching rows (may be empty)."""
    if filters is None:
        return list(rows)

    since_d = _parse_iso_date(filters.since)
    until_d = _parse_iso_date(filters.until)
    session = (filters.session or "").strip().lower()
    decision = (filters.decision or "").strip().upper()
    review_level = (filters.review_level or "").strip().lower()
    opportunity_status = (filters.opportunity_status or "").strip().upper()
    strategy = (filters.strategy or "").strip()

    out: list[dict[str, Any]] = []
    for row in rows:
        sig = signal_date(row)
        sig_d = _parse_iso_date(sig)
        if since_d and (sig_d is None or sig_d < since_d):
            continue
        if until_d and (sig_d is None or sig_d > until_d):
            continue
        if session and _group_value(row, "session").lower() != session:
            continue
        if decision and _group_value(row, "decision").upper() != decision:
            continue
        if review_level and _group_value(row, "review_level").lower() != review_level:
            continue
        if opportunity_status and _group_value(row, "opportunity_status").upper() != opportunity_status:
            continue
        if strategy and _group_value(row, "strategy") != strategy:
            continue
        if filters.exclude_legacy and is_legacy_outcome_row(row):
            continue
        if filters.only_valid_geometry and not has_valid_geometry(row):
            continue
        if filters.only_planned and not has_planned_entry(row):
            continue
        if filters.only_conditional and not is_conditional_entry(row):
            continue
        if filters.only_closed and not is_closed_outcome(row):
            continue
        out.append(row)
    return out


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def _sum(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values), 4)


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


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
        return {
            "total_rows": self.rows,
            "rows": self.rows,
            "valid_rows": self.valid_rows,
            "invalid_rows": self.invalid,
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
            "expectancy_closed": avg_r_closed,
            "entry_rate": _rate(self.entries_triggered, self.valid_rows),
            "closed_rate": _rate(closed, self.entries_triggered),
            "no_entry_rate": _rate(self.no_entry, self.valid_rows),
            "open_rate": _rate(self.open_count, self.entries_triggered),
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
        for raw in reader:
            if not raw:
                continue
            row = enrich_run_identity(dict(raw))
            row["_source_file"] = path.name
            rows.append(row)
    logger.info("Loaded %s rows from %s", len(rows), path)
    return rows


def discover_outcome_csvs(
    input_path: Path,
    *,
    include_archive: bool = False,
) -> list[Path]:
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
    if include_archive:
        archive_dir = input_path / "archive"
        if archive_dir.is_dir():
            files.extend(
                sorted(
                    p
                    for p in archive_dir.glob("*_outcomes.csv")
                    if p.is_file()
                )
            )
    if not files:
        raise ValueError(f"No *_outcomes.csv files in {input_path}")
    return files


def _outcome_row_dedupe_key(row: dict[str, Any]) -> tuple[str, ...]:
    """Conservative dedupe key when top-level and archive share the same run."""
    run_id = _group_value(row, "run_id")
    ticker = _group_value(row, "ticker").upper()
    date_val = _group_value(row, "date")[:10]
    session = _group_value(row, "session")
    if run_id:
        return ("run", run_id, ticker, date_val, session)
    source = str(row.get("_source_file") or "").strip()
    return ("legacy", ticker, date_val, session, source)


def dedupe_outcome_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop duplicate rows loaded from latest + archive sources (keep first)."""
    seen: set[tuple[str, ...]] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        key = _outcome_row_dedupe_key(row)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def load_outcome_rows(
    input_path: Path,
    *,
    include_archive: bool = False,
) -> list[dict[str, Any]]:
    """Load one or many outcome CSV files."""
    files = discover_outcome_csvs(input_path, include_archive=include_archive)
    rows: list[dict[str, Any]] = []
    for path in files:
        try:
            rows.extend(load_outcome_csv(path))
        except OSError as exc:
            logger.warning("Skipping unreadable outcome file %s: %s", path, exc)
    if include_archive:
        rows = dedupe_outcome_rows(rows)
    if not rows:
        raise ValueError(f"No outcome rows loaded from {input_path}")
    return rows


def aggregate_outcome_rows(
    rows: Iterable[dict[str, Any]],
    *,
    group_keys: Sequence[str],
    filter_label: str = "",
) -> list[dict[str, Any]]:
    """Aggregate outcome rows by the given dimension keys."""
    buckets: dict[tuple[str, ...], _Accumulator] = defaultdict(_Accumulator)
    for row in rows:
        key = _group_key(row, group_keys)
        buckets[key].add(row)

    out: list[dict[str, Any]] = []
    for key in sorted(buckets.keys()):
        record: dict[str, Any] = {"filter_label": filter_label}
        for i, gkey in enumerate(group_keys):
            record[gkey] = key[i]
        record.update(buckets[key].to_metrics())
        out.append(record)
    return out


def build_summary_tables(
    rows: list[dict[str, Any]],
    *,
    filter_label: str = "",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (overall summary, by-run summary) row lists."""
    overall = aggregate_outcome_rows(
        rows, group_keys=SUMMARY_GROUP_KEYS, filter_label=filter_label
    )
    by_run = aggregate_outcome_rows(
        rows, group_keys=BY_RUN_GROUP_KEYS, filter_label=filter_label
    )
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
    filters: BacktestSummaryFilters | None = None,
    include_archive: bool = False,
) -> tuple[Path, Path, list[dict[str, Any]], list[dict[str, Any]], int, int]:
    """Load outcome CSVs, apply filters, write summary CSVs.

    Returns (summary_path, by_run_path, overall, by_run, raw_loaded, filtered_count).
    """
    in_dir = (input_path or default_backtests_dir()).resolve()
    out_summary = (output_path or in_dir / "summary.csv").resolve()
    out_by_run = (by_run_output_path or in_dir / "summary_by_run.csv").resolve()

    raw_rows = load_outcome_rows(in_dir, include_archive=include_archive)
    raw_loaded = len(raw_rows)
    rows = apply_summary_filters(raw_rows, filters)
    filter_label = filters.label() if filters else ""

    if rows:
        overall, by_run = build_summary_tables(rows, filter_label=filter_label)
    else:
        overall, by_run = [], []
        logger.warning(
            "Backtest summary filters matched zero rows (raw_loaded=%s, filters=%s)",
            raw_loaded,
            filter_label or "none",
        )

    export_summary_csv(overall, output_path=out_summary, columns=SUMMARY_CSV_COLUMNS)
    export_summary_csv(by_run, output_path=out_by_run, columns=BY_RUN_CSV_COLUMNS)

    logger.info(
        "Backtest summary wrote %s overall groups to %s and %s by-run groups to %s "
        "(raw_loaded=%s, filtered=%s, source=%s)",
        len(overall),
        out_summary,
        len(by_run),
        out_by_run,
        raw_loaded,
        len(rows),
        in_dir,
    )
    return out_summary, out_by_run, overall, by_run, raw_loaded, len(rows)


def _top_closed_edge_group(
    groups: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Best group by expectancy_closed, then total_r_closed, then closed count."""
    candidates = [
        g
        for g in groups
        if (g.get("wins", 0) + g.get("losses", 0)) > 0
        and g.get("expectancy_closed") is not None
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda g: (
            g.get("expectancy_closed") if g.get("expectancy_closed") is not None else float("-inf"),
            g.get("total_r_closed") if g.get("total_r_closed") is not None else float("-inf"),
            g.get("wins", 0) + g.get("losses", 0),
        ),
    )


def format_summary_console(
    overall: list[dict[str, Any]],
    by_run: list[dict[str, Any]],
    *,
    raw_loaded: int,
    filtered_rows: int,
    filters: BacktestSummaryFilters | None = None,
) -> str:
    """Compact console summary for CLI."""
    lines = [
        "Backtest summary aggregator",
        f"  raw outcome rows loaded: {raw_loaded}",
        f"  rows after filters: {filtered_rows}",
    ]
    if filters and filters.label():
        lines.append(f"  active filters: {filters.label()}")
    lines.extend(
        [
            f"  overall groups: {len(overall)}",
            f"  by-run groups: {len(by_run)}",
        ]
    )
    if filtered_rows == 0:
        lines.append("  no rows matched filters; empty summary CSVs written")
        return "\n".join(lines)

    top = _top_closed_edge_group(overall)
    if top:
        lines.append(
            "  top closed-edge group: "
            f"session={top.get('session')} decision={top.get('decision')} "
            f"opportunity_status={top.get('opportunity_status')} "
            f"closed={top.get('wins', 0) + top.get('losses', 0)} "
            f"expectancy_closed={top.get('expectancy_closed')} "
            f"total_r_closed={top.get('total_r_closed')}"
        )
    else:
        closed_groups = [g for g in overall if (g.get("wins", 0) + g.get("losses", 0)) > 0]
        if closed_groups:
            top_wr = max(
                closed_groups,
                key=lambda r: (r.get("wins", 0) + r.get("losses", 0), r.get("rows", 0)),
            )
            lines.append(
                "  top closed-trade group (by count): "
                f"session={top_wr.get('session')} decision={top_wr.get('decision')} "
                f"opportunity_status={top_wr.get('opportunity_status')} "
                f"wins={top_wr.get('wins')} losses={top_wr.get('losses')} "
                f"win_rate_closed={top_wr.get('win_rate_closed')}"
            )
    return "\n".join(lines)


def filters_from_namespace(args: Any) -> BacktestSummaryFilters:
    """Build filters from argparse namespace for summarize-backtests."""
    return BacktestSummaryFilters(
        since=getattr(args, "since", None),
        until=getattr(args, "until", None),
        session=getattr(args, "session", None),
        decision=getattr(args, "decision", None),
        review_level=getattr(args, "review_level", None),
        opportunity_status=getattr(args, "opportunity_status", None),
        strategy=getattr(args, "strategy", None),
        only_valid_geometry=bool(getattr(args, "only_valid_geometry", False)),
        only_planned=bool(getattr(args, "only_planned", False)),
        only_conditional=bool(getattr(args, "only_conditional", False)),
        only_closed=bool(getattr(args, "only_closed", False)),
        exclude_legacy=bool(getattr(args, "exclude_legacy", False)),
    )
