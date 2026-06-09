from __future__ import annotations

import csv
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

logger = logging.getLogger(__name__)

_SUMMARY_OUTPUT_NAMES = frozenset({"summary.csv", "summary_by_run.csv"})

_IDENTITY_COLUMNS = (
    "date",
    "session",
    "run_timestamp_utc",
    "run_id",
    "ticker",
    "source_type",
    "source_file",
)

_MASTER_PRIORITY_TAIL = (
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
    "resolved_rr",
    "entry_zone",
    "stop_loss",
    "target",
    "planned_entry_price",
    "conditional_buy_limit",
    "valid_entry_max",
    "current_rr",
    "rr_gap",
    "opportunity_status",
    "opportunity_note",
    "revisit_opportunity_status",
    "revisit_planned_entry_price",
    "revisit_conditional_buy_limit",
    "resolved_planned_entry",
    "market_regime",
    "tech_bias",
    "overall_risk_level",
    "reason",
    "action_required",
    "revisit_condition",
    "has_outcome",
    "entry_triggered",
    "exit_type",
    "entry_date",
    "exit_date",
    "exit_price",
    "r_multiple",
    "mfe",
    "mae",
    "bars_held",
    "note",
    "is_actionable_candidate",
    "is_cio_reviewed",
    "is_rank_excluded",
    "is_buy",
    "is_watch",
    "is_pass",
    "is_screened",
    "is_conditional",
)

REVIEW_MASTER_COLUMNS = _IDENTITY_COLUMNS + _MASTER_PRIORITY_TAIL
OPPORTUNITY_MASTER_COLUMNS = REVIEW_MASTER_COLUMNS
OUTCOMES_MASTER_COLUMNS = _IDENTITY_COLUMNS + _MASTER_PRIORITY_TAIL
SIGNAL_MASTER_COLUMNS = _IDENTITY_COLUMNS + _MASTER_PRIORITY_TAIL

_OUTCOME_FIELD_MAP = {
    "entry_triggered": "entry_triggered",
    "exit_type": "exit_type",
    "entry_date": "entry_date",
    "exit_date": "exit_date",
    "exit_price": "exit_price",
    "r_multiple": "r_multiple",
    "mfe": "max_favourable_excursion",
    "mae": "max_adverse_excursion",
    "bars_held": "bars_held",
    "note": "outcome_note",
}


@dataclass
class ResearchDatasetSummary:
    review_loaded: int = 0
    review_written: int = 0
    opportunity_loaded: int = 0
    opportunity_written: int = 0
    outcome_loaded: int = 0
    outcome_written: int = 0
    signal_written: int = 0
    unique_run_ids: int = 0
    unique_tickers: int = 0
    by_source_type: dict[str, int] = field(default_factory=dict)
    by_decision: dict[str, int] = field(default_factory=dict)
    by_session: dict[str, int] = field(default_factory=dict)
    has_outcome_count: int = 0
    entry_triggered_count: int = 0
    preview_rows: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def default_research_dir() -> Path:
    return Path.cwd().resolve() / "data" / "research"


def default_input_root() -> Path:
    return Path.cwd().resolve() / "data"


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _truthy(value: Any) -> bool:
    return _text(value).lower() in ("true", "1", "yes")


def _load_csv_rows(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            if raw:
                rows.append({k: _text(v) for k, v in raw.items()})
    return rows


def _discover_csv_files(
    directory: Path,
    pattern: str,
    *,
    exclude_names: frozenset[str] | None = None,
) -> list[Path]:
    if not directory.is_dir():
        return []
    excluded = exclude_names or frozenset()
    return sorted(
        p
        for p in directory.glob(pattern)
        if p.is_file() and p.name not in excluded
    )


def discover_review_sources(
    input_root: Path,
    *,
    include_latest: bool = False,
) -> list[Path]:
    paths: list[Path] = []
    paths.extend(_discover_csv_files(input_root / "reviews" / "archive", "*_review.csv"))
    if include_latest:
        paths.extend(_discover_csv_files(input_root / "reviews", "*_review.csv"))
    return paths


def discover_opportunity_sources(
    input_root: Path,
    *,
    include_latest: bool = False,
) -> list[Path]:
    paths: list[Path] = []
    paths.extend(
        _discover_csv_files(
            input_root / "opportunities" / "archive",
            "*_opportunities.csv",
        )
    )
    if include_latest:
        paths.extend(
            _discover_csv_files(input_root / "opportunities", "*_opportunities.csv")
        )
    return paths


def discover_outcome_sources(
    input_root: Path,
    *,
    include_latest: bool = False,
) -> list[Path]:
    paths: list[Path] = []
    paths.extend(
        _discover_csv_files(
            input_root / "backtests" / "archive",
            "*_outcomes.csv",
        )
    )
    if include_latest:
        paths.extend(
            _discover_csv_files(
                input_root / "backtests",
                "*_outcomes.csv",
                exclude_names=_SUMMARY_OUTPUT_NAMES,
            )
        )
    return paths


def outcome_source_type_from_filename(filename: str) -> str:
    name = filename.lower()
    if "_review_outcomes" in name:
        return "review_outcome"
    if "_opportunities_outcomes" in name:
        return "opportunity_outcome"
    return "outcome"


def _ensure_identity_columns(row: dict[str, str]) -> dict[str, str]:
    out = dict(row)
    for col in ("date", "session", "run_timestamp_utc", "run_id", "ticker"):
        out.setdefault(col, "")
    return out


def _annotate_row(
    row: dict[str, str],
    *,
    source_file: str,
    source_type: str,
) -> dict[str, str]:
    out = _ensure_identity_columns(row)
    out["source_file"] = source_file
    out["source_type"] = source_type
    return out


def master_dedupe_key(row: dict[str, str]) -> tuple[str, ...]:
    run_id = _text(row.get("run_id"))
    ticker = _text(row.get("ticker")).upper()
    source_type = _text(row.get("source_type"))
    if run_id:
        return ("run", run_id, ticker, source_type)
    source_file = _text(row.get("source_file"))
    return ("file", source_file, ticker, source_type)


def dedupe_master_rows(rows: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, ...]] = set()
    out: list[dict[str, str]] = []
    for row in rows:
        key = master_dedupe_key(row)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _load_annotated_rows(
    paths: Sequence[Path],
    *,
    source_type: str | None = None,
    source_type_from_filename: bool = False,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in paths:
        stype = (
            outcome_source_type_from_filename(path.name)
            if source_type_from_filename
            else (source_type or "")
        )
        for row in _load_csv_rows(path):
            rows.append(
                _annotate_row(row, source_file=path.name, source_type=stype)
            )
    return rows


def _ordered_columns(
    rows: Sequence[dict[str, str]],
    priority: Sequence[str],
) -> list[str]:
    seen: set[str] = set()
    columns: list[str] = []
    for col in priority:
        if col not in seen:
            columns.append(col)
            seen.add(col)
    for row in rows:
        for col in row:
            if col not in seen:
                columns.append(col)
                seen.add(col)
    return columns


def write_master_csv(
    rows: Sequence[dict[str, str]],
    output_path: Path,
    *,
    priority_columns: Sequence[str],
) -> int:
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    columns = _ordered_columns(rows, priority_columns)
    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in columns})
    return len(rows)


def _resolved_planned_entry(row: dict[str, str]) -> str:
    for key in ("planned_entry_price", "revisit_planned_entry_price"):
        value = _text(row.get(key))
        if value:
            return value
    return ""


def _resolved_rr(row: dict[str, str]) -> str:
    for key in ("risk_reward", "model_risk_reward", "revisit_current_rr"):
        value = _text(row.get(key))
        if value:
            return value
    return ""


def _is_actionable_candidate(row: dict[str, str]) -> bool:
    if _text(row.get("direction")).lower() != "long":
        return False
    if not _truthy(row.get("math_valid")):
        return False
    return bool(_text(row.get("stop_loss")) and _text(row.get("target")))


def _decision_flags(row: dict[str, str]) -> dict[str, str]:
    decision = _text(row.get("decision")).upper()
    return {
        "is_buy": "true" if decision == "BUY" else "false",
        "is_watch": "true" if decision == "WATCH" else "false",
        "is_pass": "true" if decision == "PASS" else "false",
        "is_screened": "true" if decision == "SCREENED" else "false",
    }


def _index_outcomes(
    outcome_rows: Sequence[dict[str, str]],
) -> dict[tuple[str, str], list[dict[str, str]]]:
    indexed: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in outcome_rows:
        run_id = _text(row.get("run_id"))
        ticker = _text(row.get("ticker")).upper()
        if run_id and ticker:
            indexed[(run_id, ticker)].append(row)
    return indexed


def _find_outcome_row(
    outcomes_by_key: dict[tuple[str, str], list[dict[str, str]]],
    *,
    run_id: str,
    ticker: str,
    signal_source_type: str,
) -> dict[str, str] | None:
    candidates = outcomes_by_key.get((run_id, ticker.upper()), [])
    if not candidates:
        return None
    preferred = (
        "opportunity_outcome"
        if signal_source_type == "opportunity"
        else "review_outcome"
    )
    for row in candidates:
        if _text(row.get("source_type")) == preferred:
            return row
    return candidates[0]


def _apply_outcome_fields(
    signal_row: dict[str, str],
    outcome_row: dict[str, str] | None,
) -> dict[str, str]:
    out = dict(signal_row)
    if outcome_row is None:
        out["has_outcome"] = "false"
        for field in _OUTCOME_FIELD_MAP:
            out.setdefault(field, "")
        return out

    out["has_outcome"] = "true"
    for dest, src in _OUTCOME_FIELD_MAP.items():
        out[dest] = _text(outcome_row.get(src))
    return out


def _build_signal_row(
    row: dict[str, str],
    *,
    signal_source_type: str,
    outcome_row: dict[str, str] | None,
) -> dict[str, str]:
    out = _ensure_identity_columns(dict(row))
    out["source_type"] = signal_source_type
    out.setdefault("source_file", _text(row.get("source_file")))
    out["resolved_planned_entry"] = _resolved_planned_entry(out)
    out["resolved_rr"] = _resolved_rr(out)
    out["is_actionable_candidate"] = (
        "true" if _is_actionable_candidate(out) else "false"
    )
    out["is_cio_reviewed"] = (
        "true" if _text(out.get("review_level")) == "cio_reviewed" else "false"
    )
    out["is_rank_excluded"] = (
        "true" if _text(out.get("review_level")) == "rank_excluded" else "false"
    )
    out.update(_decision_flags(out))
    out["is_conditional"] = (
        "true"
        if _truthy(out.get("conditional_buy_limit"))
        or _truthy(out.get("revisit_conditional_buy_limit"))
        else "false"
    )
    return _apply_outcome_fields(out, outcome_row)


def build_signal_master(
    review_rows: Sequence[dict[str, str]],
    opportunity_rows: Sequence[dict[str, str]],
    outcome_rows: Sequence[dict[str, str]],
) -> list[dict[str, str]]:
    outcomes_by_key = _index_outcomes(outcome_rows)
    signals: dict[tuple[str, str], dict[str, str]] = {}

    for row in opportunity_rows:
        run_id = _text(row.get("run_id"))
        ticker = _text(row.get("ticker")).upper()
        if not ticker:
            continue
        key = (run_id, ticker)
        outcome = _find_outcome_row(
            outcomes_by_key,
            run_id=run_id,
            ticker=ticker,
            signal_source_type="opportunity",
        )
        signals[key] = _build_signal_row(
            row,
            signal_source_type="opportunity",
            outcome_row=outcome,
        )

    for row in review_rows:
        run_id = _text(row.get("run_id"))
        ticker = _text(row.get("ticker")).upper()
        if not ticker:
            continue
        key = (run_id, ticker)
        if key in signals:
            continue
        outcome = _find_outcome_row(
            outcomes_by_key,
            run_id=run_id,
            ticker=ticker,
            signal_source_type="review",
        )
        signals[key] = _build_signal_row(
            row,
            signal_source_type="review",
            outcome_row=outcome,
        )

    return list(signals.values())


def _preview_sort_key(row: dict[str, str]) -> tuple[str, str, str]:
    rank = _text(row.get("analysis_rank"))
    try:
        rank_key = f"{int(float(rank)):010d}"
    except ValueError:
        rank_key = rank
    return (_text(row.get("date")), _text(row.get("run_id")), rank_key)


def _build_summary_stats(
    signal_rows: Sequence[dict[str, str]],
) -> tuple[
    int,
    int,
    dict[str, int],
    dict[str, int],
    dict[str, int],
    int,
    int,
    list[dict[str, str]],
]:
    run_ids = {_text(r.get("run_id")) for r in signal_rows if _text(r.get("run_id"))}
    tickers = {_text(r.get("ticker")).upper() for r in signal_rows if _text(r.get("ticker"))}
    by_source_type = Counter(_text(r.get("source_type")) or "unknown" for r in signal_rows)
    by_decision = Counter(_text(r.get("decision")) or "unknown" for r in signal_rows)
    by_session = Counter(_text(r.get("session")) or "unknown" for r in signal_rows)
    has_outcome_count = sum(1 for r in signal_rows if _truthy(r.get("has_outcome")))
    entry_triggered_count = sum(
        1 for r in signal_rows if _truthy(r.get("entry_triggered"))
    )
    preview = sorted(signal_rows, key=_preview_sort_key)[:5]
    preview_rows = [
        {col: _text(row.get(col)) for col in (
            "date",
            "session",
            "run_id",
            "ticker",
            "source_type",
            "review_level",
            "analysis_rank",
            "decision",
            "opportunity_status",
            "strategy",
            "has_outcome",
            "entry_triggered",
            "exit_type",
            "r_multiple",
        )}
        for row in preview
    ]
    return (
        len(run_ids),
        len(tickers),
        dict(by_source_type),
        dict(by_decision),
        dict(by_session),
        has_outcome_count,
        entry_triggered_count,
        preview_rows,
    )


def format_research_dataset_summary(summary: ResearchDatasetSummary) -> str:
    lines = [
        "**Research dataset build**",
        f"- review rows loaded: {summary.review_loaded}",
        f"- review rows written: {summary.review_written}",
        f"- opportunity rows loaded: {summary.opportunity_loaded}",
        f"- opportunity rows written: {summary.opportunity_written}",
        f"- outcome rows loaded: {summary.outcome_loaded}",
        f"- outcome rows written: {summary.outcome_written}",
        f"- signal rows written: {summary.signal_written}",
        f"- unique run_ids: {summary.unique_run_ids}",
        f"- unique tickers: {summary.unique_tickers}",
        f"- by source_type: {summary.by_source_type}",
        f"- by decision: {summary.by_decision}",
        f"- by session: {summary.by_session}",
        f"- has_outcome: {summary.has_outcome_count}",
        f"- entry_triggered: {summary.entry_triggered_count}",
    ]
    if summary.warnings:
        lines.append("- warnings:")
        lines.extend(f"  - {w}" for w in summary.warnings)
    if summary.preview_rows:
        lines.append("- preview (top 5 by date/run_id/analysis_rank):")
        for row in summary.preview_rows:
            lines.append(
                "  - "
                + ", ".join(f"{k}={v}" for k, v in row.items() if v != "")
            )
    return "\n".join(lines)


def run_build_research_dataset(
    *,
    input_root: Path | None = None,
    output_dir: Path | None = None,
    include_latest: bool = False,
    dedupe: bool = True,
) -> tuple[ResearchDatasetSummary, dict[str, Path]]:
    root = (input_root or default_input_root()).resolve()
    out_dir = (output_dir or default_research_dir()).resolve()
    summary = ResearchDatasetSummary()

    review_paths = discover_review_sources(root, include_latest=include_latest)
    opportunity_paths = discover_opportunity_sources(root, include_latest=include_latest)
    outcome_paths = discover_outcome_sources(root, include_latest=include_latest)

    if not review_paths:
        summary.warnings.append(f"No review CSV sources found under {root / 'reviews'}")
    if not opportunity_paths:
        summary.warnings.append(
            f"No opportunity CSV sources found under {root / 'opportunities'}"
        )
    if not outcome_paths:
        summary.warnings.append(f"No outcome CSV sources found under {root / 'backtests'}")

    review_rows = _load_annotated_rows(review_paths, source_type="review")
    opportunity_rows = _load_annotated_rows(opportunity_paths, source_type="opportunity")
    outcome_rows = _load_annotated_rows(
        outcome_paths,
        source_type_from_filename=True,
    )

    summary.review_loaded = len(review_rows)
    summary.opportunity_loaded = len(opportunity_rows)
    summary.outcome_loaded = len(outcome_rows)

    if dedupe:
        review_rows = dedupe_master_rows(review_rows)
        opportunity_rows = dedupe_master_rows(opportunity_rows)
        outcome_rows = dedupe_master_rows(outcome_rows)

    signal_rows = build_signal_master(review_rows, opportunity_rows, outcome_rows)

    outputs = {
        "review_master": out_dir / "review_master.csv",
        "opportunity_master": out_dir / "opportunity_master.csv",
        "outcomes_master": out_dir / "outcomes_master.csv",
        "signal_master": out_dir / "signal_master.csv",
    }

    summary.review_written = write_master_csv(
        review_rows,
        outputs["review_master"],
        priority_columns=REVIEW_MASTER_COLUMNS,
    )
    summary.opportunity_written = write_master_csv(
        opportunity_rows,
        outputs["opportunity_master"],
        priority_columns=OPPORTUNITY_MASTER_COLUMNS,
    )
    summary.outcome_written = write_master_csv(
        outcome_rows,
        outputs["outcomes_master"],
        priority_columns=OUTCOMES_MASTER_COLUMNS,
    )
    summary.signal_written = write_master_csv(
        signal_rows,
        outputs["signal_master"],
        priority_columns=SIGNAL_MASTER_COLUMNS,
    )

    (
        summary.unique_run_ids,
        summary.unique_tickers,
        summary.by_source_type,
        summary.by_decision,
        summary.by_session,
        summary.has_outcome_count,
        summary.entry_triggered_count,
        summary.preview_rows,
    ) = _build_summary_stats(signal_rows)

    for warning in summary.warnings:
        logger.warning(warning)

    return summary, outputs
