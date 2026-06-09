from __future__ import annotations

import csv
import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from swingtrade.outcome_tracker import (
    EXIT_DATA_ERROR,
    EXIT_INVALID,
    EXIT_LOSS,
    EXIT_NO_ENTRY,
    EXIT_OPEN,
    EXIT_WIN,
)
from swingtrade.research_dataset import dedupe_master_rows, default_research_dir
from swingtrade.trade_math import _as_float

logger = logging.getLogger(__name__)

GROUP_DIMENSIONS = (
    "decision",
    "review_level",
    "opportunity_status",
    "strategy",
    "session",
    "ticker",
    "market_regime",
    "source_type",
)

DIAGNOSTICS_CSV_COLUMNS = (
    "report_section",
    "dimension",
    "dimension_value",
    "rows",
    "unique_run_ids",
    "unique_tickers",
    "unique_dates",
    "wins",
    "losses",
    "no_entry",
    "open",
    "invalid",
    "data_error",
    "no_outcome",
    "closed_trades",
    "entries_triggered",
    "win_rate_closed",
    "avg_r_closed",
    "total_r_closed",
    "expectancy_closed",
    "avg_r_triggered",
    "entry_rate",
    "no_entry_rate",
    "closed_rate",
    "confidence",
)

COHORT_FILTERS: dict[str, Callable[[dict[str, str]], bool]] = {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _truthy(value: Any) -> bool:
    return _text(value).lower() in ("true", "1", "yes")


def _normalize_exit_type(row: dict[str, str]) -> str:
    return _text(row.get("exit_type")).upper()


def _entry_triggered(row: dict[str, str]) -> bool:
    return _truthy(row.get("entry_triggered"))


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


def confidence_label(closed_trades: int) -> str:
    if closed_trades < 20:
        return "LOW"
    if closed_trades < 50:
        return "EARLY"
    if closed_trades < 100:
        return "DEVELOPING"
    return "MORE_RELIABLE"


def has_valid_geometry(row: dict[str, str]) -> bool:
    exit_type = _normalize_exit_type(row)
    if exit_type in (EXIT_INVALID, EXIT_DATA_ERROR):
        return False
    stop = _as_float(row.get("stop_loss"))
    target = _as_float(row.get("target"))
    entry = _as_float(row.get("planned_entry_price"))
    if entry is None:
        entry = _as_float(row.get("valid_entry_max"))
    if entry is None:
        entry = _as_float(row.get("resolved_planned_entry"))
    return stop is not None and target is not None and entry is not None


def _is_conditional(row: dict[str, str]) -> bool:
    return _truthy(row.get("is_conditional")) or _truthy(row.get("conditional_buy_limit"))


def _signal_date(row: dict[str, str]) -> str:
    date_val = _text(row.get("date"))
    if date_val:
        return date_val[:10]
    run_id = _text(row.get("run_id"))
    if len(run_id) >= 10 and run_id[4] == "-" and run_id[7] == "-":
        return run_id[:10]
    return ""


@dataclass
class _MetricsAccumulator:
    rows: int = 0
    valid_rows: int = 0
    entries_triggered: int = 0
    wins: int = 0
    losses: int = 0
    no_entry: int = 0
    open_count: int = 0
    invalid: int = 0
    data_error: int = 0
    no_outcome: int = 0
    r_triggered: list[float] = field(default_factory=list)
    r_closed: list[float] = field(default_factory=list)

    def add(self, row: dict[str, str]) -> None:
        self.rows += 1
        if not _truthy(row.get("has_outcome")):
            self.no_outcome += 1
            return

        exit_type = _normalize_exit_type(row)
        if exit_type == EXIT_DATA_ERROR:
            self.data_error += 1
            return
        if exit_type == EXIT_INVALID:
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

    def to_record(
        self,
        *,
        report_section: str,
        dimension: str,
        dimension_value: str,
        rows: Sequence[dict[str, str]],
    ) -> dict[str, Any]:
        closed = self.wins + self.losses
        win_rate = round(self.wins / closed * 100.0, 2) if closed else None
        run_ids = {_text(r.get("run_id")) for r in rows if _text(r.get("run_id"))}
        tickers = {_text(r.get("ticker")).upper() for r in rows if _text(r.get("ticker"))}
        dates = {_signal_date(r) for r in rows if _signal_date(r)}
        return {
            "report_section": report_section,
            "dimension": dimension,
            "dimension_value": dimension_value,
            "rows": self.rows,
            "unique_run_ids": len(run_ids),
            "unique_tickers": len(tickers),
            "unique_dates": len(dates),
            "wins": self.wins,
            "losses": self.losses,
            "no_entry": self.no_entry,
            "open": self.open_count,
            "invalid": self.invalid,
            "data_error": self.data_error,
            "no_outcome": self.no_outcome,
            "closed_trades": closed,
            "entries_triggered": self.entries_triggered,
            "win_rate_closed": win_rate,
            "avg_r_closed": _avg(self.r_closed),
            "total_r_closed": _sum(self.r_closed),
            "expectancy_closed": _avg(self.r_closed),
            "avg_r_triggered": _avg(self.r_triggered),
            "entry_rate": _rate(self.entries_triggered, self.valid_rows),
            "no_entry_rate": _rate(self.no_entry, self.valid_rows),
            "closed_rate": _rate(closed, self.entries_triggered),
            "confidence": confidence_label(closed),
        }


def _accumulate(rows: Iterable[dict[str, str]]) -> _MetricsAccumulator:
    acc = _MetricsAccumulator()
    for row in rows:
        acc.add(row)
    return acc


def _metrics_row(
    rows: Sequence[dict[str, str]],
    *,
    report_section: str,
    dimension: str,
    dimension_value: str,
) -> dict[str, Any]:
    acc = _accumulate(rows)
    return acc.to_record(
        report_section=report_section,
        dimension=dimension,
        dimension_value=dimension_value,
        rows=rows,
    )


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return str(value)
    return str(value)


def _load_csv_rows(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            if raw:
                rows.append({k: _text(v) for k, v in raw.items()})
    return rows


def load_signal_master(path: Path) -> tuple[list[dict[str, str]], int]:
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Signal master not found: {path}")
    loaded = _load_csv_rows(path)
    deduped = dedupe_master_rows(loaded)
    return deduped, len(loaded)


def _group_rows(
    rows: Sequence[dict[str, str]],
    dimension: str,
) -> dict[str, list[dict[str, str]]]:
    buckets: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        value = _text(row.get(dimension)) or "unknown"
        buckets.setdefault(value, []).append(row)
    return buckets


def _filter_rows(
    rows: Sequence[dict[str, str]],
    predicate: Callable[[dict[str, str]], bool],
) -> list[dict[str, str]]:
    return [row for row in rows if predicate(row)]


def _register_cohort_filters() -> None:
    if COHORT_FILTERS:
        return
    for decision in ("BUY", "WATCH", "PASS", "SCREENED"):
        COHORT_FILTERS[f"decision:{decision}"] = (
            lambda row, d=decision: _text(row.get("decision")).upper() == d
        )
    COHORT_FILTERS["review_level:cio_reviewed"] = (
        lambda row: _text(row.get("review_level")) == "cio_reviewed"
    )
    COHORT_FILTERS["review_level:rank_excluded"] = (
        lambda row: _text(row.get("review_level")) == "rank_excluded"
    )
    COHORT_FILTERS["source_type:opportunity"] = (
        lambda row: _text(row.get("source_type")) == "opportunity"
    )
    COHORT_FILTERS["source_type:review"] = (
        lambda row: _text(row.get("source_type")) == "review"
    )
    for status in (
        "BUY_NOW",
        "NEAR_BUY",
        "PULLBACK_REQUIRED",
        "NO_ACTIONABLE_ZONE",
    ):
        COHORT_FILTERS[f"opportunity_status:{status}"] = (
            lambda row, s=status: _text(row.get("opportunity_status")).upper() == s
        )
    COHORT_FILTERS["geometry:valid"] = has_valid_geometry
    COHORT_FILTERS["geometry:invalid"] = lambda row: not has_valid_geometry(row)
    COHORT_FILTERS["conditional:true"] = _is_conditional
    COHORT_FILTERS["conditional:false"] = lambda row: not _is_conditional(row)


def build_diagnostics_rows(rows: Sequence[dict[str, str]]) -> list[dict[str, Any]]:
    _register_cohort_filters()
    out: list[dict[str, Any]] = []

    out.append(
        _metrics_row(rows, report_section="overall", dimension="overall", dimension_value="all")
    )

    for dimension in GROUP_DIMENSIONS:
        for value, bucket in sorted(_group_rows(rows, dimension).items()):
            out.append(
                _metrics_row(
                    bucket,
                    report_section="group",
                    dimension=dimension,
                    dimension_value=value,
                )
            )

    for cohort_key, predicate in sorted(COHORT_FILTERS.items()):
        dimension, value = cohort_key.split(":", 1)
        bucket = _filter_rows(rows, predicate)
        out.append(
            _metrics_row(
                bucket,
                report_section="cohort",
                dimension=dimension,
                dimension_value=value,
            )
        )

    return out


@dataclass
class ResearchDiagnosticsSummary:
    input_path: Path
    output_csv: Path
    output_markdown: Path | None
    rows_loaded: int
    rows_deduped: int
    overall: dict[str, Any]
    overall_metrics: dict[str, Any]
    decisions: Counter[str]
    review_levels: Counter[str]
    opportunity_statuses: Counter[str]
    sessions: Counter[str]
    confidence: str
    warnings: list[str] = field(default_factory=list)


def _overall_counts(rows: Sequence[dict[str, str]]) -> dict[str, Any]:
    run_ids = {_text(r.get("run_id")) for r in rows if _text(r.get("run_id"))}
    dates = {_signal_date(r) for r in rows if _signal_date(r)}
    tickers = {_text(r.get("ticker")).upper() for r in rows if _text(r.get("ticker"))}
    return {
        "rows": len(rows),
        "unique_run_ids": len(run_ids),
        "unique_dates": len(dates),
        "unique_tickers": len(tickers),
        "sessions": dict(Counter(_text(r.get("session")) or "unknown" for r in rows)),
        "decisions": dict(Counter(_text(r.get("decision")) or "unknown" for r in rows)),
        "review_levels": dict(
            Counter(_text(r.get("review_level")) or "unknown" for r in rows)
        ),
        "opportunity_statuses": dict(
            Counter(_text(r.get("opportunity_status")) or "unknown" for r in rows)
        ),
    }


def write_diagnostics_csv(rows: list[dict[str, Any]], output_path: Path) -> Path:
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=DIAGNOSTICS_CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({col: _cell(row.get(col)) for col in DIAGNOSTICS_CSV_COLUMNS})
    return output_path


def format_diagnostics_markdown(
    summary: ResearchDiagnosticsSummary,
    diagnostics_rows: Sequence[dict[str, Any]],
) -> str:
    overall = summary.overall
    lines = [
        "# Research edge diagnostics",
        "",
        "_Diagnostics only — not trading advice. Sample sizes may be too small "
        "to infer durable edge._",
        "",
        f"- Input: `{summary.input_path}`",
        f"- Rows (deduped): {summary.rows_deduped}",
        f"- Unique run_ids: {overall['unique_run_ids']}",
        f"- Unique dates: {overall['unique_dates']}",
        f"- Unique tickers: {overall['unique_tickers']}",
        f"- Confidence (closed trades): **{summary.confidence}**",
        "",
        "## Overall counts",
        f"- sessions: {summary.sessions}",
        f"- decisions: {summary.decisions}",
        f"- review_levels: {summary.review_levels}",
        f"- opportunity_statuses: {summary.opportunity_statuses}",
        "",
        "## Closed-only performance (WIN/LOSS only)",
    ]
    o = next(r for r in diagnostics_rows if r.get("report_section") == "overall")
    lines.extend(
        [
            f"- closed_trades: {o.get('closed_trades')}",
            f"- win_rate_closed: {o.get('win_rate_closed')}",
            f"- avg_r_closed: {o.get('avg_r_closed')}",
            f"- total_r_closed: {o.get('total_r_closed')}",
            f"- expectancy_closed: {o.get('expectancy_closed')}",
            "",
            "## Triggered performance",
            f"- entries_triggered: {o.get('entries_triggered')}",
            f"- avg_r_triggered: {o.get('avg_r_triggered')}",
            "",
            "## Outcome funnel (valid rows)",
            f"- wins: {o.get('wins')}",
            f"- losses: {o.get('losses')}",
            f"- no_entry: {o.get('no_entry')}",
            f"- open: {o.get('open')}",
            f"- invalid: {o.get('invalid')}",
            f"- data_error: {o.get('data_error')}",
            f"- entry_rate: {o.get('entry_rate')}",
            f"- no_entry_rate: {o.get('no_entry_rate')}",
            "",
            "## Key cohort snapshots",
        ]
    )
    for row in diagnostics_rows:
        if row.get("report_section") != "cohort":
            continue
        lines.append(
            f"- {row.get('dimension')}={row.get('dimension_value')}: "
            f"rows={row.get('rows')}, closed={row.get('closed_trades')}, "
            f"win_rate_closed={row.get('win_rate_closed')}, "
            f"expectancy_closed={row.get('expectancy_closed')}, "
            f"confidence={row.get('confidence')}"
        )
    if summary.warnings:
        lines.extend(["", "## Warnings"])
        lines.extend(f"- {w}" for w in summary.warnings)
    return "\n".join(lines) + "\n"


def write_diagnostics_markdown(path: Path, content: str) -> Path:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def format_diagnostics_console(summary: ResearchDiagnosticsSummary) -> str:
    overall = summary.overall
    o = summary.overall_metrics
    lines = [
        "**Research edge diagnostics**",
        f"- input: {summary.input_path}",
        f"- rows loaded/deduped: {summary.rows_loaded}/{summary.rows_deduped}",
        f"- unique run_ids: {overall['unique_run_ids']}",
        f"- unique dates: {overall['unique_dates']}",
        f"- unique tickers: {overall['unique_tickers']}",
        f"- sessions: {dict(summary.sessions)}",
        f"- decisions: {dict(summary.decisions)}",
        f"- review_levels: {dict(summary.review_levels)}",
        f"- opportunity_statuses: {dict(summary.opportunity_statuses)}",
        f"- closed_trades: {o.get('closed_trades')}",
        f"- win_rate_closed: {o.get('win_rate_closed')}",
        f"- expectancy_closed: {o.get('expectancy_closed')}",
        f"- entries_triggered: {o.get('entries_triggered')}",
        f"- avg_r_triggered: {o.get('avg_r_triggered')}",
        f"- confidence: {summary.confidence}",
        f"- output csv: {summary.output_csv}",
    ]
    if summary.output_markdown:
        lines.append(f"- output markdown: {summary.output_markdown}")
    if summary.warnings:
        lines.append("- warnings:")
        lines.extend(f"  - {w}" for w in summary.warnings)
    lines.append(
        "_Diagnostics only. OPEN/NO_ENTRY excluded from closed win rate; "
        "INVALID/DATA_ERROR excluded from performance metrics._"
    )
    return "\n".join(lines)


def run_research_edge_diagnostics(
    *,
    input_path: Path | None = None,
    output_csv: Path | None = None,
    output_markdown: Path | None = None,
    write_markdown: bool = True,
) -> tuple[ResearchDiagnosticsSummary, list[dict[str, Any]]]:
    research_dir = default_research_dir()
    in_path = (input_path or research_dir / "signal_master.csv").resolve()
    out_csv = (output_csv or research_dir / "edge_diagnostics.csv").resolve()
    out_md = (
        (output_markdown or research_dir / "edge_diagnostics.md").resolve()
        if write_markdown
        else None
    )

    loaded, raw_count = load_signal_master(in_path)
    diagnostics_rows = build_diagnostics_rows(loaded)
    write_diagnostics_csv(diagnostics_rows, out_csv)

    overall_row = next(r for r in diagnostics_rows if r["report_section"] == "overall")
    counts = _overall_counts(loaded)
    closed = int(overall_row.get("closed_trades") or 0)
    confidence = confidence_label(closed)
    warnings: list[str] = []
    if confidence == "LOW":
        warnings.append(
            f"Closed trades ({closed}) < 20 — treat all performance metrics as LOW confidence."
        )
    elif confidence == "EARLY":
        warnings.append(
            f"Closed trades ({closed}) between 20-49 — EARLY sample; avoid overfitting conclusions."
        )

    summary = ResearchDiagnosticsSummary(
        input_path=in_path,
        output_csv=out_csv,
        output_markdown=out_md,
        rows_loaded=raw_count,
        rows_deduped=len(loaded),
        overall=counts,
        overall_metrics=overall_row,
        decisions=Counter(counts["decisions"]),
        review_levels=Counter(counts["review_levels"]),
        opportunity_statuses=Counter(counts["opportunity_statuses"]),
        sessions=Counter(counts["sessions"]),
        confidence=confidence,
        warnings=warnings,
    )

    if out_md is not None:
        md = format_diagnostics_markdown(summary, diagnostics_rows)
        write_diagnostics_markdown(out_md, md)

    for warning in warnings:
        logger.warning(warning)

    return summary, diagnostics_rows


def default_signal_master_path() -> Path:
    return default_research_dir() / "signal_master.csv"


def default_edge_diagnostics_csv() -> Path:
    return default_research_dir() / "edge_diagnostics.csv"


def default_edge_diagnostics_md() -> Path:
    return default_research_dir() / "edge_diagnostics.md"
